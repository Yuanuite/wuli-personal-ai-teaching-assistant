#!/usr/bin/env python3
"""Compare direct-model, teacher-console web, and teacher-reviewed answers.

The direct model output is a baseline, not ground truth.  The current
teacher-reviewed ``student-solution.md`` is the reference artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))

import analysis_artifacts  # noqa: E402
import teacher_feedback  # noqa: E402

LEVEL_ORDER = ("基础", "较易", "中等", "较难", "挑战")
FORMULA = re.compile(
    r"\$\$(.+?)\$\$|\$([^$\n]+)\$|\\\[(.+?)\\\]|\\\((.+?)\\\)",
    re.DOTALL,
)
CORRECT_OPTION_PATTERNS = (
    re.compile(r"正确选项[：:]\s*(?:\*\*)?\s*([A-F](?:[\s、,，/及和]*[A-F])*)", re.I),
    re.compile(r"最终选择\s*(?:\$\$?)?\s*\\boxed\{([A-F]+)\}", re.I),
)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_formula(value: str) -> str:
    value = re.sub(r"\\(?:dfrac|tfrac)", r"\\frac", value)
    return re.sub(r"\s+|\\(?:left|right|quad|qquad|,|;|!)", "", value)


def formulae(text: str) -> set[str]:
    values = set()
    for match in FORMULA.finditer(text):
        raw = next((group for group in match.groups() if group), "")
        normalized = normalize_formula(raw)
        if normalized and len(normalized) >= 3:
            values.add(normalized)
    return values


def correct_options(text: str) -> set[str]:
    """Read an explicit final selection without treating formula letters as options."""
    for pattern in CORRECT_OPTION_PATTERNS:
        match = pattern.search(text[:3000])
        if match:
            return {letter.upper() for letter in re.findall(r"[A-F]", match.group(1), re.I)}
    return set()


def compare(candidate: str, reference: str) -> dict[str, Any]:
    expected_options = correct_options(reference)
    actual_options = correct_options(candidate)
    option_match = None
    if expected_options:
        if not actual_options:
            actual_options = {
                label
                for label, verdict in analysis_artifacts._option_verdicts(candidate[:2400]).items()
                if verdict
            }
        option_match = actual_options == expected_options
    else:
        expected_verdicts = analysis_artifacts._option_verdicts(reference[:2400])
        actual_verdicts = analysis_artifacts._option_verdicts(candidate[:2400])
        if expected_verdicts:
            option_match = actual_verdicts == expected_verdicts
            expected_options = set(expected_verdicts)
    expected_formulae = formulae(reference)
    actual_formulae = formulae(candidate)
    retained = expected_formulae & actual_formulae
    semantic = teacher_feedback.semantic_text_diff(candidate, reference, path="student-solution.md")
    return {
        "option_verdict_match": option_match,
        "expected_option_count": len(expected_options),
        "formula_recall": (
            round(len(retained) / len(expected_formulae), 4) if expected_formulae else None
        ),
        "reference_formula_count": len(expected_formulae),
        "candidate_formula_count": len(actual_formulae),
        "semantic_change_ratio": round(
            semantic["changed_lines"] / max(semantic["total_lines"], 1), 4
        ),
        "critical_correction": semantic["critical_correction"],
        "candidate_chars": len(candidate),
        "reference_chars": len(reference),
    }


def eligible_entries(library: Path) -> list[dict[str, Any]]:
    rows = []
    for entry in sorted((library / "entries").iterdir()):
        if not entry.is_dir():
            continue
        record = load_json(entry / "record.json")
        review = record.get("answer_review", {})
        solution = entry / "student-solution.md"
        if review.get("status") != "passed" or not solution.is_file():
            continue
        difficulty = record.get("difficulty_assessment", {})
        rows.append({
            "entry_id": entry.name,
            "title": str(record.get("title", entry.name)),
            "difficulty_score": difficulty.get("score"),
            "difficulty_level": str(difficulty.get("level", record.get("difficulty", "未分级"))),
            "reference_digest": digest_text(solution.read_text(encoding="utf-8")),
        })
    return rows


def seed(library: Path, experiment: Path, per_level: int) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible_entries(library):
        grouped[row["difficulty_level"]].append(row)
    cases = []
    for level in LEVEL_ORDER:
        rows = sorted(
            grouped.get(level, []),
            key=lambda item: (abs(float(item["difficulty_score"] or 0) - 50), item["entry_id"]),
        )
        cases.extend(rows[:per_level])
    experiment.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "kind": "paired-answer-benchmark",
        "ground_truth": "teacher-reviewed-student-solution",
        "cohorts": ["direct", "web"],
        "cases": cases,
    }
    (experiment / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prompts = experiment / "prompts"
    prompts.mkdir(exist_ok=True)
    for case in cases:
        problem = (library / "entries" / case["entry_id"] / "problem.md").read_text(encoding="utf-8")
        (prompts / f"{case['entry_id']}.txt").write_text(
            "请直接阅读下列高中物理题，独立给出正确答案和简洁高中范围推导。"
            "不要使用题库检索、RAG、既有答案、工作流模板或工具。\n\n" + problem,
            encoding="utf-8",
        )
    return manifest


def refresh_references(library: Path, experiment: Path) -> dict[str, int]:
    """Accept the latest teacher-passed answers as benchmark truth."""
    manifest_path = experiment / "manifest.json"
    manifest = load_json(manifest_path)
    refreshed = skipped = 0
    for case in manifest.get("cases", []):
        entry = library / "entries" / str(case.get("entry_id", ""))
        record = load_json(entry / "record.json")
        solution = entry / "student-solution.md"
        if (
            record.get("answer_review", {}).get("status") != "passed"
            or not solution.is_file()
        ):
            skipped += 1
            continue
        difficulty = record.get("difficulty_assessment", {})
        case.update({
            "title": str(record.get("title", case.get("title", entry.name))),
            "difficulty_score": difficulty.get("score", case.get("difficulty_score")),
            "difficulty_level": str(
                difficulty.get(
                    "level",
                    record.get("difficulty", case.get("difficulty_level", "未分级")),
                )
            ),
            "reference_digest": digest_text(solution.read_text(encoding="utf-8")),
        })
        refreshed += 1
    manifest["ground_truth"] = "teacher-reviewed-student-solution"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"refreshed": refreshed, "skipped": skipped}


def capture_web(library: Path, experiment: Path) -> dict[str, int]:
    """Import historical Agent baselines for inspection, not paired scoring.

    A baseline snapshot proves that an Agent answer existed, but it does not
    prove that this experiment produced it through a browser click.  Therefore
    imported artifacts deliberately receive non-ready provenance.
    """
    manifest = load_json(experiment / "manifest.json")
    captured = missing = 0
    for case in manifest.get("cases", []):
        entry_id = case["entry_id"]
        source = library / "entries" / entry_id / ".agent-baseline" / "student-solution.md"
        if not source.is_file():
            missing += 1
            continue
        target = experiment / "artifacts" / entry_id / "web.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        (target.parent / "web.meta.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "entry_id": entry_id,
                    "source": "canonical-agent-baseline-import",
                    "status": "inspection-only",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        captured += 1
    return {"captured": captured, "missing": missing}


def browser_web_ready(candidate_path: Path) -> tuple[bool, str]:
    if not candidate_path.is_file():
        return False, "missing"
    metadata = load_json(candidate_path.with_name("web.meta.json"))
    if metadata.get("source") != "teacher-console-browser-click":
        return False, str(metadata.get("source", "missing-provenance"))
    if metadata.get("status") != "completed":
        return False, str(metadata.get("status", "incomplete"))
    return True, "teacher-console-browser-click"


def evaluate(library: Path, experiment: Path) -> dict[str, Any]:
    manifest = load_json(experiment / "manifest.json")
    records = []
    readiness = []
    for case in manifest.get("cases", []):
        entry_id = case["entry_id"]
        reference_path = library / "entries" / entry_id / "student-solution.md"
        if not reference_path.is_file():
            continue
        reference = reference_path.read_text(encoding="utf-8")
        stale_reference = digest_text(reference) != case.get("reference_digest")
        available = {}
        provenance = {}
        for cohort in ("direct", "web"):
            candidate_path = experiment / "artifacts" / entry_id / f"{cohort}.md"
            if cohort == "web":
                available[cohort], provenance[cohort] = browser_web_ready(candidate_path)
            else:
                available[cohort] = candidate_path.is_file()
                provenance[cohort] = "direct-model-output" if available[cohort] else "missing"
            if not available[cohort]:
                continue
            record = {
                **case,
                "cohort": cohort,
                "stale_reference": stale_reference,
                "metrics": compare(candidate_path.read_text(encoding="utf-8"), reference),
            }
            if cohort == "web":
                metadata = load_json(candidate_path.with_name("web.meta.json"))
                evidence = metadata.get("evidence_context", {})
                record["generation"] = {
                    "source": metadata.get("source"),
                    "model_id": metadata.get("model_id"),
                    "provider": metadata.get("provider"),
                    "routing_tier": metadata.get("routing_tier"),
                    "evidence_status": evidence.get("status"),
                    "evidence_reference_count": evidence.get("reference_count"),
                }
            records.append(record)
        direct_input_status = case.get("direct_input_status", "needs-review")
        pair_ready = all(available.values())
        readiness.append({
            "entry_id": entry_id,
            "difficulty_level": case["difficulty_level"],
            "direct": available.get("direct", False),
            "web": available.get("web", False),
            "pair_ready": pair_ready,
            "comparison_ready": (
                pair_ready and direct_input_status == "complete" and not stale_reference
            ),
            "direct_provenance": provenance.get("direct", "missing"),
            "web_provenance": provenance.get("web", "missing"),
            "direct_input_status": direct_input_status,
            "stale_reference": stale_reference,
        })
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(record["difficulty_level"], record["cohort"])].append(record)
    summary = []
    for (level, cohort), items in sorted(
        groups.items(),
        key=lambda pair: (
            LEVEL_ORDER.index(pair[0][0]) if pair[0][0] in LEVEL_ORDER else 99,
            pair[0][1],
        ),
    ):
        metrics = [item["metrics"] for item in items]
        option_values = [item["option_verdict_match"] for item in metrics if item["option_verdict_match"] is not None]
        formula_values = [item["formula_recall"] for item in metrics if item["formula_recall"] is not None]
        summary.append({
            "difficulty_level": level,
            "cohort": cohort,
            "count": len(items),
            "option_match_rate": round(sum(option_values) / len(option_values), 4) if option_values else None,
            "formula_recall_avg": round(sum(formula_values) / len(formula_values), 4) if formula_values else None,
            "critical_correction_rate": round(
                sum(bool(item["critical_correction"]) for item in metrics) / len(metrics), 4
            ),
            "semantic_change_ratio_avg": round(
                sum(item["semantic_change_ratio"] for item in metrics) / len(metrics), 4
            ),
        })
    return {
        "schema_version": 1,
        "ground_truth": "teacher-reviewed-student-solution",
        "direct_is_reference_baseline_only": True,
        "human_review_required": True,
        "readiness": {
            "case_count": len(readiness),
            "pair_ready_count": sum(item["pair_ready"] for item in readiness),
            "comparison_ready_count": sum(item["comparison_ready"] for item in readiness),
            "missing_direct": sum(not item["direct"] for item in readiness),
            "missing_web": sum(not item["web"] for item in readiness),
            "stale_reference_count": sum(item["stale_reference"] for item in readiness),
            "cases": readiness,
        },
        "records": records,
        "summary": summary,
    }


def print_markdown(report: dict[str, Any]) -> None:
    print("# Paired Answer Benchmark")
    print()
    print("- ground truth: teacher-reviewed student solution")
    print("- direct output is a baseline, not a standard answer")
    ready = report["readiness"]
    print(
        f"- pair readiness: {ready['pair_ready_count']}/{ready['case_count']}; "
        f"comparison ready={ready['comparison_ready_count']}; "
        f"missing direct={ready['missing_direct']}; missing web={ready['missing_web']}"
    )
    print()
    print("| difficulty | cohort | n | option match | formula recall | critical correction | semantic diff |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for item in report["summary"]:
        print(
            f"| {item['difficulty_level']} | {item['cohort']} | {item['count']} | "
            f"{item['option_match_rate']} | {item['formula_recall_avg']} | "
            f"{item['critical_correction_rate']} | {item['semantic_change_ratio_avg']} |"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=PROJECT_ROOT / "student-error-library")
    parser.add_argument("--experiment", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("--per-level", type=int, default=2)
    sub.add_parser("refresh-references")
    sub.add_parser("capture-web")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    library = args.library.expanduser().resolve()
    experiment = args.experiment.expanduser().resolve()
    if args.command == "seed":
        result = seed(library, experiment, max(1, args.per_level))
    elif args.command == "refresh-references":
        result = refresh_references(library, experiment)
    elif args.command == "capture-web":
        result = capture_web(library, experiment)
    else:
        result = evaluate(library, experiment)
        if args.format == "markdown":
            print_markdown(result)
            return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
