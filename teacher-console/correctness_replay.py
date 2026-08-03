#!/usr/bin/env python3
"""Read-only diagnostic replay of legacy W3 outputs through current contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import claim_ledger
import claim_validation
import correctness_faults
import solution_reasoning

REPLAY_SCHEMA = "wuli.correctness-replay-diagnostic.v1"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"required replay artifact is missing: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"replay artifact must be an object: {path.name}")
    return payload


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def diagnose_replay(
    library: Path,
    experiment: Path,
    fault_cases: list[dict[str, Any]],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Project frozen legacy candidates; never call providers or write entries."""
    manifest = _load(experiment / "manifest.json")
    truth_lock = _load(experiment / "truth-lock.json")
    legacy_result = _load(experiment / "result.json")
    paired_result = _load(experiment / "paired-result.json")
    cases = manifest.get("cases")
    if manifest.get("schema_version") != 2 or not isinstance(cases, list):
        raise ValueError("replay manifest must use frozen schema version 2")
    if not cases or any(item.get("evaluation_split") != "replay" for item in cases):
        raise ValueError("diagnostic replay accepts replay-only cases")
    if truth_lock.get("case_count") != len(cases):
        raise ValueError("truth lock case count does not match manifest")
    if paired_result.get("production_evidence") is not False:
        raise ValueError("legacy replay must not claim production evidence")
    if paired_result.get("errors"):
        raise ValueError("legacy paired replay contains blocking errors")

    locked = {str(item.get("entry_id", "")): item for item in truth_lock.get("cases", []) if isinstance(item, dict)}
    paired = {str(item.get("entry_id", "")): item for item in paired_result.get("pairs", []) if isinstance(item, dict)}
    rows = []
    for case in cases:
        entry_id = str(case.get("entry_id", ""))
        if entry_id not in locked or entry_id not in paired:
            raise ValueError(f"replay case lacks frozen truth or pair: {entry_id}")
        entry = library / "entries" / entry_id
        answer = entry / "student-solution.md"
        canonical_before = _digest(answer)
        reference_digest_current = canonical_before == case.get("reference_digest")
        shadow = _load(entry / "w3-shadow-report.json")
        report = shadow.get("report")
        if shadow.get("status") != "completed" or not isinstance(report, dict):
            raise ValueError(f"legacy W3 report is incomplete: {entry_id}")
        blueprint = report.get("blueprint")
        solver = report.get("solver_a")
        if not isinstance(blueprint, dict) or not isinstance(solver, dict):
            raise ValueError(f"legacy W3 solve artifacts are missing: {entry_id}")
        projection_input = {
            "entry_id": entry_id,
            "blueprint": blueprint,
            "solver_a": solver,
        }
        input_fingerprint = claim_ledger.stable_fingerprint("ce504-legacy-replay-v1", projection_input)
        snapshot = solution_reasoning.project_claim_ledger(
            solver,
            blueprint,
            input_fingerprint=input_fingerprint,
        )
        replayed = solution_reasoning.project_claim_ledger(
            solver,
            blueprint,
            input_fingerprint=input_fingerprint,
        )
        if snapshot != replayed:
            raise ValueError(f"Claim projection is not replayable: {entry_id}")
        target_ids = {
            str(item.get("id", "")).strip()
            for item in blueprint.get("question_targets", [])
            if str(item.get("id", "")).strip()
        }
        obligation_ids = {
            str(item.get("id", "")).strip()
            for item in blueprint.get("verification_obligations", [])
            if str(item.get("id", "")).strip()
        }
        evidence = claim_validation.evaluate_claim_graph_evidence(
            snapshot["claims"],
            [],
            expected_target_ids=target_ids,
            expected_obligation_ids=obligation_ids,
        )
        if evidence["result_status"] == "VERIFIED":
            raise ValueError(f"legacy projection without certificates was mis-promoted: {entry_id}")
        active = claim_ledger.active_claims(snapshot["claims"])
        rows.append({
            "entry_id": entry_id,
            "legacy_claim_evidence_present": ("claim_evidence_shadow" in report),
            "claim_count": len(active),
            "target_count": len(target_ids),
            "obligation_count": len(obligation_ids),
            "semantic_required_claim_count": sum(
                isinstance(item["check_spec"], dict) and item["check_spec"].get("type") == "semantic-required"
                for item in active.values()
            ),
            "projected_status_without_certificates": evidence["result_status"],
            "critical_certificate_coverage": evidence["critical_certificate_coverage"],
            "projection_replayable": True,
            "canonical_unchanged": canonical_before == _digest(answer),
            "reference_digest_current": reference_digest_current,
            "w2_correct_target_count": int(paired[entry_id]["w2_correct_target_count"]),
            "w3_correct_target_count": int(paired[entry_id]["w3_correct_target_count"]),
        })

    fault_outcomes = correctness_faults.run_fault_suite(fault_cases)
    fault_detected = sum(item["detected"] for item in fault_outcomes)
    projected_targets = sum(item["target_count"] for item in rows)
    locked_targets = int(truth_lock.get("target_count", 0))
    w2_correct = sum(item["w2_correct_target_count"] for item in rows)
    w3_correct = sum(item["w3_correct_target_count"] for item in rows)
    metrics = {
        "case_count": len(rows),
        "target_count": projected_targets,
        "obligation_count": sum(item["obligation_count"] for item in rows),
        "projected_claim_count": sum(item["claim_count"] for item in rows),
        "semantic_required_claim_count": sum(item["semantic_required_claim_count"] for item in rows),
        "projected_case_count": len(rows),
        "projection_replay_rate": round(
            sum(item["projection_replayable"] for item in rows) / len(rows),
            6,
        ),
        "provisional_without_certificate_count": sum(
            item["projected_status_without_certificates"] == "PROVISIONAL" for item in rows
        ),
        "legacy_claim_evidence_report_count": sum(item["legacy_claim_evidence_present"] for item in rows),
        "canonical_mutation_count": sum(not item["canonical_unchanged"] for item in rows),
        "reference_digest_mismatch_count": sum(not item["reference_digest_current"] for item in rows),
        "w2_correct_target_count": w2_correct,
        "w3_correct_target_count": w3_correct,
        "known_w3_gain_target_count": w3_correct - w2_correct,
        "known_w3_gain_case_count": sum(
            item["w3_correct_target_count"] > item["w2_correct_target_count"] for item in rows
        ),
        "fault_case_count": len(fault_outcomes),
        "fault_detected_count": fault_detected,
        "fault_detection_rate": round(fault_detected / len(fault_outcomes), 6),
        "false_promotion_count": sum(item["false_promotion"] for item in fault_outcomes),
    }
    gates = {
        "truth_lock_intact": projected_targets == locked_targets,
        "all_legacy_cases_projected": len(rows) == len(cases),
        "projection_replayable": all(item["projection_replayable"] for item in rows),
        "legacy_outputs_not_auto_trusted": all(
            item["projected_status_without_certificates"] != "VERIFIED" for item in rows
        ),
        "all_declared_faults_detected": fault_detected == len(fault_outcomes),
        "zero_false_promotion": metrics["false_promotion_count"] == 0,
        "canonical_unchanged": metrics["canonical_mutation_count"] == 0,
        "production_authorized": False,
    }
    return {
        "schema_version": REPLAY_SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "experiment_id": manifest.get("experiment_id"),
        "evaluation_split": "replay",
        "metrics": metrics,
        "gates": gates,
        "legacy_result_gates": legacy_result.get("gates", {}),
        "readiness": {
            "historical_scores_reusable_without_refresh": (metrics["reference_digest_mismatch_count"] == 0),
            "reference_refresh_required": (metrics["reference_digest_mismatch_count"] > 0),
        },
        "cases": rows,
        "limitations": [
            "历史输出产生于 Claim Evidence 接入前，当前步骤只做确定性投影。",
            "无当前版本证书的投影全部保持 PROVISIONAL，旧答案分数不会自动变成证书。",
            "旧题已参与开发与教师复核，只能用于链路和已知故障诊断。",
            "本报告不能替代 fresh holdout，也不能授权生产。",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# 教学正确性证据链旧题 replay 诊断 v1",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 实验：`{report['experiment_id']}`",
        f"- 旧题 / 冻结目标：{metrics['case_count']} / {metrics['target_count']}",
        f"- 当前 Claim 投影：{metrics['projected_claim_count']}；义务：{metrics['obligation_count']}",
        f"- 投影可重放率：{metrics['projection_replay_rate']:.1%}",
        f"- 无证书时 PROVISIONAL：{metrics['provisional_without_certificate_count']}/{metrics['case_count']}",
        f"- 旧报告自带 Claim Evidence：{metrics['legacy_claim_evidence_report_count']}/{metrics['case_count']}",
        f"- 当前答案摘要与旧 manifest 不一致：{metrics['reference_digest_mismatch_count']}/{metrics['case_count']}",
        f"- 历史 W2 / W3 目标正确数：{metrics['w2_correct_target_count']}/{metrics['w3_correct_target_count']}",
        f"- 已知 W3 增益目标 / 题：{metrics['known_w3_gain_target_count']} / {metrics['known_w3_gain_case_count']}",
        f"- 当前故障集检出：{metrics['fault_detected_count']}/"
        f"{metrics['fault_case_count']}；错误晋升："
        f"{metrics['false_promotion_count']}",
        "",
        "## 门禁",
        "",
    ]
    for name, passed in report["gates"].items():
        lines.append(f"- `{name}`：{str(passed).lower()}")
    lines.extend(["", "## 历史分数可复用性", ""])
    for name, value in report["readiness"].items():
        lines.append(f"- `{name}`：{str(value).lower()}")
    lines.extend(["", "## 解释边界", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"
