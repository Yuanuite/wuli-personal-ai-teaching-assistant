"""Paired baseline/Evidence-Agent calibration benchmark."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import evidence_agent
import evidence_contract
import evidence_evaluation

REPORT_SCHEMA = "wuli.evidence-shadow-benchmark.v1"


def baseline_prediction(
    gold_case: dict[str, Any],
    candidate_pool: dict[str, Any],
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    """Model the pre-Agent Top-K behavior without semantic sufficiency checks."""
    need = gold_case["retrieval_need"]
    ranked = []
    for query in candidate_pool.get("queries", []):
        if query.get("need_id") == need["need_id"]:
            ranked.extend(query.get("candidate_evidence_ids", []))
    candidates = list(dict.fromkeys(ranked))
    selected = candidates[: max(1, min(int(top_k), 20))]
    if need["criticality"] == "optional":
        status = "not_needed"
        selected = []
    else:
        status = "sufficient" if selected else "insufficient"
    return evidence_evaluation.normalize_prediction({
        "case_id": gold_case["case_id"],
        "status": status,
        "candidate_evidence_ids": candidates,
        "selected_evidence_ids": selected,
        "traceable_evidence_ids": selected,
    })


def _gateway_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    attempts = []
    for item in result.get("attempts", []):
        if not isinstance(item, dict):
            continue
        attempts.append({
            "provider": item.get("provider"),
            "returncode": item.get("returncode"),
            "failure_type": item.get("failure_type"),
            "stderr": str(item.get("stderr") or "")[:1000],
            "stdout": str(item.get("stdout") or "")[:500],
        })
    return {
        "status": result.get("status"),
        "provider": result.get("provider"),
        "model_id": result.get("model_id"),
        "failure_type": result.get("failure_type"),
        "duration_seconds": result.get("duration_seconds"),
        "usage": {key: usage[key] for key in ("input_tokens", "output_tokens", "total_tokens") if key in usage},
        "changed_files": result.get("changed_files", []),
        "message": str(result.get("message") or "")[:1000],
        "attempts": attempts,
    }


def run_paired_benchmark(
    dataset_payload: dict[str, Any],
    *,
    library_root: Path,
    gateway: Any,
    top_k: int = 5,
    routing_tier: str = "expert",
    model_config: dict[str, Any] | None = None,
    max_cases: int | None = None,
    allow_provider_dependent_data: bool = False,
    projection_overlay: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a calibration set with the same deterministic candidate pool."""
    dataset = evidence_evaluation.normalize_gold_dataset(dataset_payload)
    if projection_overlay is not None:
        normalized_overlay = [evidence_contract.normalize_evidence_unit(item) for item in projection_overlay]
        overlay_fingerprint = evidence_contract.stable_fingerprint("evidence-overlay-v1", normalized_overlay)
        if dataset.get("evidence_snapshot_fingerprint") != overlay_fingerprint:
            raise ValueError("dataset evidence snapshot fingerprint does not match overlay")
        projection_overlay = normalized_overlay
    elif dataset.get("evidence_snapshot_fingerprint"):
        raise ValueError("dataset requires an evidence overlay")
    cases = dataset["cases"]
    if max_cases is not None:
        cases = cases[: max(1, int(max_cases))]
        # A truncated run is diagnostic and receives a derived draft envelope.
        dataset = evidence_evaluation.normalize_gold_dataset({
            **dataset,
            "dataset_version": dataset["dataset_version"] + "-partial",
            "review_status": "draft",
            "reviewer": "",
            "reviewed_at": "",
            "dataset_fingerprint": None,
            "cases": cases,
        })

    baseline_predictions = []
    agent_predictions = []
    executions = []
    with tempfile.TemporaryDirectory(prefix="wuli-evidence-calibration-") as directory:
        root = Path(directory)
        for index, item in enumerate(cases, 1):
            gold_case = item["gold_case"]
            entry = root / f"case-{index:03d}"
            entry.mkdir()
            (entry / "problem.md").write_text(item["problem"], encoding="utf-8")
            execution = evidence_agent.run_shadow(
                gateway,
                library_root=library_root,
                entry=entry,
                problem=item["problem"],
                blueprint=item["blueprint"],
                retrieval_needs=[gold_case["retrieval_need"]],
                top_k_per_need=top_k,
                routing_tier=routing_tier,
                model_config=model_config,
                allow_remote=allow_provider_dependent_data,
                source_kinds=tuple(dataset["source_scope"]),
                projection_overlay=projection_overlay,
            )
            baseline_predictions.append(baseline_prediction(gold_case, execution["candidate_pool"], top_k=top_k))
            agent_predictions.append(evidence_evaluation.prediction_from_run(gold_case["case_id"], execution))
            executions.append({
                "case_id": gold_case["case_id"],
                "status": execution["status"],
                "candidate_evidence_ids": [
                    unit["evidence_id"] for unit in execution["candidate_pool"].get("candidates", [])
                ],
                "gateway": _gateway_summary(execution.get("gateway_result")),
                "coverage": execution["evidence_agent_run"].get("coverage", []),
                "insufficient_evidence": execution["evidence_agent_run"].get("insufficient_evidence"),
            })
    paired = evidence_evaluation.paired_gold_report(dataset, baseline_predictions, agent_predictions)
    return {
        "schema": REPORT_SCHEMA,
        "data_scope": {
            "model_visible_source_kinds": dataset["source_scope"],
            "student_entry_evidence_sent": False,
            "raw_source_assets_sent": False,
            "provider_data_locality": "provider-dependent",
            "provider_dependent_data_acknowledged": bool(allow_provider_dependent_data),
        },
        "candidate_policy": evidence_agent.FUSION_POLICY,
        "top_k": top_k,
        "paired": paired,
        "executions": executions,
    }
