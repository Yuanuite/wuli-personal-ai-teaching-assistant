#!/usr/bin/env python3
"""Paired shadow evaluation for one frozen W3R Brief."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

import w3_rendering  # noqa: E402
import w3r_contract  # noqa: E402


def _baseline_from_same_brief(brief: dict[str, Any]) -> str:
    """Model the old concise renderer without reading any other answer source."""
    lines = ["## 答案速览", ""]
    for index, item in enumerate(brief["final_answers"], 1):
        lines.append(f"- （{index}）{item['text']}")
    lines.extend(["", "## 详细解答", ""])
    for index, step in enumerate(brief["proof_skeleton"][:5], 1):
        lines.extend([f"### 第 {index} 步", "", step["statement"], ""])
    return "\n".join(lines).rstrip() + "\n"


def evaluate_case(case_id: str, brief_payload: Any) -> dict[str, Any]:
    brief = w3r_contract.normalize_w3r_brief(brief_payload)
    fingerprint = w3r_contract.brief_fingerprint(brief)
    baseline = _baseline_from_same_brief(brief)
    started = time.perf_counter()
    candidate = w3_rendering.render_w3r(brief)
    elapsed = round(time.perf_counter() - started, 6)
    gate = candidate["render_gate_report"]
    student = candidate["student_solution_md"]
    return {
        "case_id": case_id,
        "brief_fingerprint": fingerprint,
        "target_count": len(brief["question_targets"]),
        "paired_input_identical": candidate["brief_fingerprint"] == fingerprint,
        "baseline": {
            "character_count": len(baseline),
            "section_count": baseline.count("## "),
            "latex_block_count": baseline.count("$$") // 2,
        },
        "candidate": {
            "status": candidate["status"],
            "character_count": len(student),
            "section_count": student.count("## "),
            "latex_block_count": student.count("$$") // 2,
            "gate_status": gate["status"],
            "metrics": gate["metrics"],
            "violation_codes": [str(item.get("code", "")) for item in gate["violations"]],
            "render_attempts": candidate["attempt"],
            "elapsed_seconds": elapsed,
            "model_call_count": 0,
            "token_count": 0,
        },
        "production_replaced": False,
    }


def benchmark(cases: list[tuple[str, Any]]) -> dict[str, Any]:
    rows = [evaluate_case(case_id, brief) for case_id, brief in cases]
    hard_metric_names = (
        "final_answer_fidelity",
        "claim_support_coverage",
        "condition_retention",
        "target_coverage",
        "latex_validity",
    )
    hard_pass = bool(rows) and all(
        row["paired_input_identical"]
        and row["candidate"]["gate_status"] == "pass"
        and all(row["candidate"]["metrics"].get(name) == 1.0 for name in hard_metric_names)
        and row["candidate"]["metrics"].get("unsupported_claim_rate") == 0.0
        for row in rows
    )
    return {
        "schema": "wuli.w3r-shadow-benchmark.v1",
        "mode": "shadow",
        "case_count": len(rows),
        "cases": rows,
        "gates": {
            "final_answer_fidelity_100": hard_pass
            and all(row["candidate"]["metrics"]["final_answer_fidelity"] == 1.0 for row in rows),
            "claim_support_coverage_100": hard_pass
            and all(row["candidate"]["metrics"]["claim_support_coverage"] == 1.0 for row in rows),
            "condition_retention_100": hard_pass
            and all(row["candidate"]["metrics"]["condition_retention"] == 1.0 for row in rows),
            "target_coverage_100": hard_pass
            and all(row["candidate"]["metrics"]["target_coverage"] == 1.0 for row in rows),
            "latex_validity_100": hard_pass
            and all(row["candidate"]["metrics"]["latex_validity"] == 1.0 for row in rows),
            "unsupported_claim_rate_zero": hard_pass
            and all(row["candidate"]["metrics"]["unsupported_claim_rate"] == 0.0 for row in rows),
            "shadow_candidate_eligible": hard_pass,
            "production_default_eligible": False,
        },
    }


def build_blind_packet(
    cases: list[tuple[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a public A/B packet and a separate identity key."""
    packet_cases = []
    key_cases = []
    for case_id, brief_payload in cases:
        brief = w3r_contract.normalize_w3r_brief(brief_payload)
        fingerprint = w3r_contract.brief_fingerprint(brief)
        baseline = _baseline_from_same_brief(brief)
        render_result = w3_rendering.render_w3r(brief)
        candidate = render_result["student_solution_md"]
        candidate_label = "A" if int(fingerprint[-1], 16) % 2 == 0 else "B"
        baseline_label = "B" if candidate_label == "A" else "A"
        versions = {
            candidate_label: candidate,
            baseline_label: baseline,
        }
        packet_cases.append({
            "case_id": case_id,
            "versions": {"A": versions["A"], "B": versions["B"]},
            "target_ids": [item["target_id"] for item in brief["question_targets"]],
            "review_requirements": {
                "preferred_version": "A, B, or tie",
                "target_fidelity": "check every target_id",
                "conditions_checked": True,
                "claim_spans_checked_after_preference_lock": True,
                "edit_required_versions": "subset of A and B",
                "approved": True,
            },
        })
        key_cases.append({
            "case_id": case_id,
            "brief_fingerprint": fingerprint,
            "candidate_label": candidate_label,
            "baseline_label": baseline_label,
            "candidate_audit": {
                "verified_claims": brief["verified_claims"],
                "claim_span_map": render_result["claim_span_map"],
            },
        })
    packet = {
        "schema": "wuli.w3r-blind-review-packet.v1",
        "blinded": True,
        "review_sequence": [
            "lock preferred_version and edit_required_versions from A/B only",
            "open the separately held private key",
            "check candidate Claim/span mappings and submit approval",
        ],
        "cases": packet_cases,
    }
    key = {
        "schema": "wuli.w3r-blind-review-key.v1",
        "packet_fingerprint": w3r_contract.stable_fingerprint(packet),
        "cases": key_cases,
    }
    return packet, key


def score_blind_reviews(
    packet: Any,
    private_key: Any,
    reviews: Any,
) -> dict[str, Any]:
    """Validate complete teacher reviews and unblind aggregate preferences."""
    if not isinstance(packet, dict) or packet.get("blinded") is not True:
        raise ValueError("blind review packet is invalid")
    if not isinstance(private_key, dict) or private_key.get("packet_fingerprint") != w3r_contract.stable_fingerprint(
        packet
    ):
        raise ValueError("blind review key is not bound to this packet")
    review_rows = reviews.get("cases", []) if isinstance(reviews, dict) else []
    review_by_id = {item.get("case_id"): item for item in review_rows if isinstance(item, dict)}
    key_by_id = {
        item["case_id"]: item
        for item in private_key.get("cases", [])
        if isinstance(item, dict) and isinstance(item.get("case_id"), str)
    }
    errors = []
    candidate_wins = 0
    baseline_wins = 0
    ties = 0
    candidate_edits = 0
    baseline_edits = 0
    for case in packet.get("cases", []):
        case_id = case.get("case_id")
        review = review_by_id.get(case_id)
        key = key_by_id.get(case_id)
        if not isinstance(review, dict) or not isinstance(key, dict):
            errors.append(f"{case_id}: review or key missing")
            continue
        preferred = review.get("preferred_version")
        if review.get("approved") is not True:
            errors.append(f"{case_id}: teacher approval missing")
        if preferred not in {"A", "B", "tie"}:
            errors.append(f"{case_id}: preferred_version invalid")
        target_checks = review.get("target_fidelity", {})
        expected_targets = set(case.get("target_ids", []))
        if (
            not isinstance(target_checks, dict)
            or set(target_checks) != expected_targets
            or any(value is not True for value in target_checks.values())
        ):
            errors.append(f"{case_id}: target fidelity review incomplete")
        for field in ("conditions_checked", "claim_spans_checked"):
            if review.get(field) is not True:
                errors.append(f"{case_id}: {field} missing")
        edit_versions = review.get("edit_required_versions", [])
        if (
            not isinstance(edit_versions, list)
            or any(item not in {"A", "B"} for item in edit_versions)
            or len(edit_versions) != len(set(edit_versions))
        ):
            errors.append(f"{case_id}: edit_required_versions invalid")
            edit_versions = []
        candidate_edits += int(key["candidate_label"] in edit_versions)
        baseline_edits += int(key["baseline_label"] in edit_versions)
        if preferred == "tie":
            ties += 1
        elif preferred == key["candidate_label"]:
            candidate_wins += 1
        elif preferred in {"A", "B"}:
            baseline_wins += 1
    if errors:
        return {
            "schema": "wuli.w3r-blind-review-score.v1",
            "status": "incomplete",
            "errors": errors,
        }
    reviewed = len(packet.get("cases", []))
    decided = candidate_wins + baseline_wins
    return {
        "schema": "wuli.w3r-blind-review-score.v1",
        "status": "completed",
        "reviewed_case_count": reviewed,
        "candidate_wins": candidate_wins,
        "baseline_wins": baseline_wins,
        "ties": ties,
        "teacher_readability_preference": round(candidate_wins / decided, 4) if decided else 0.5,
        "candidate_edit_rate": round(candidate_edits / reviewed, 4) if reviewed else 0.0,
        "baseline_edit_rate": round(baseline_edits / reviewed, 4) if reviewed else 0.0,
        "teacher_edit_rate_non_regression": candidate_edits <= baseline_edits,
        "errors": [],
    }


def rollout_evidence(
    report: dict[str, Any],
    review_score: dict[str, Any] | None = None,
    *,
    fresh_holdout: bool = False,
) -> dict[str, Any]:
    """Create evidence fields consumed by the independent W3R router."""
    cases = report.get("cases", [])
    metrics = [row.get("candidate", {}).get("metrics", {}) for row in cases]
    completed_review = (
        review_score if isinstance(review_score, dict) and review_score.get("status") == "completed" else {}
    )

    def minimum(field: str, fallback: float) -> float:
        values = [item.get(field) for item in metrics]
        return (
            min(values)
            if values and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values)
            else fallback
        )

    target_count = sum(int(row.get("target_count", 0)) for row in cases)
    evidence = {
        "report_digest": w3r_contract.stable_fingerprint(report),
        "paired_case_count": len(cases),
        "teacher_reviewed_case_count": int(completed_review.get("reviewed_case_count", 0)),
        "fresh_holdout_case_count": len(cases) if fresh_holdout else 0,
        "fresh_holdout_target_count": target_count if fresh_holdout else 0,
        "final_answer_fidelity": minimum("final_answer_fidelity", 0.0),
        "claim_support_coverage": minimum("claim_support_coverage", 0.0),
        "condition_retention": minimum("condition_retention", 0.0),
        "target_coverage": minimum("target_coverage", 0.0),
        "latex_validity": minimum("latex_validity", 0.0),
        "unsupported_claim_rate": max(
            [item.get("unsupported_claim_rate", 1.0) for item in metrics],
            default=1.0,
        ),
        "teacher_readability_preference": float(completed_review.get("teacher_readability_preference", 0.0)),
        "teacher_edit_rate_non_regression": bool(completed_review.get("teacher_edit_rate_non_regression", False)),
    }
    return evidence


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# W3R Shadow 配对评测",
        "",
        f"- 模式：`{report['mode']}`",
        f"- 样例数：{report['case_count']}",
        f"- Shadow 硬门禁：{'通过' if report['gates']['shadow_candidate_eligible'] else '未通过'}",
        "- 生产默认：未启用",
        "",
        "| Case | 同 Brief | Gate | Final | Claim | Condition | Target | LaTeX | Unsupported |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["cases"]:
        metrics = row["candidate"]["metrics"]
        lines.append(
            f"| {row['case_id']} | {row['paired_input_identical']} | "
            f"{row['candidate']['gate_status']} | "
            f"{metrics.get('final_answer_fidelity')} | "
            f"{metrics.get('claim_support_coverage')} | "
            f"{metrics.get('condition_retention')} | "
            f"{metrics.get('target_coverage')} | "
            f"{metrics.get('latex_validity')} | "
            f"{metrics.get('unsupported_claim_rate')} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def load_case(path: Path) -> tuple[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") == w3r_contract.BRIEF_SCHEMA:
        return path.stem, payload
    built = w3r_contract.build_w3r_brief(payload["problem"], payload["blueprint"], payload["proof_package"])
    if built.get("status") != "completed":
        raise ValueError(f"{path}: {built.get('violations')}")
    return path.stem, built["brief"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--blind-packet-out", type=Path)
    parser.add_argument("--blind-key-out", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--fresh-holdout", action="store_true")
    args = parser.parse_args()
    cases = [load_case(path) for path in args.fixtures]
    report = benchmark(cases)
    rendered_json = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    rendered_md = markdown_report(report)
    if args.json_out:
        args.json_out.write_text(rendered_json, encoding="utf-8")
    else:
        print(rendered_json, end="")
    if args.md_out:
        args.md_out.write_text(rendered_md, encoding="utf-8")
    packet = key = None
    if args.blind_packet_out or args.blind_key_out or args.reviews:
        packet, key = build_blind_packet(cases)
    if args.blind_packet_out:
        args.blind_packet_out.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.blind_key_out:
        args.blind_key_out.write_text(
            json.dumps(key, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    review_score = None
    if args.reviews:
        review_score = score_blind_reviews(
            packet,
            key,
            json.loads(args.reviews.read_text(encoding="utf-8")),
        )
    if args.evidence_out:
        args.evidence_out.write_text(
            json.dumps(
                rollout_evidence(
                    report,
                    review_score,
                    fresh_holdout=args.fresh_holdout,
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0 if report["gates"]["shadow_candidate_eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
