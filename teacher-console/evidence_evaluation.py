"""Gold-case validation and three-layer metrics for Evidence Agent shadow runs."""

from __future__ import annotations

from typing import Any

import evidence_contract

GOLD_CASE_SCHEMA = "wuli.evidence-gold-case.v1"
PREDICTION_SCHEMA = "wuli.evidence-gold-prediction.v1"
GOLD_DATASET_SCHEMA = "wuli.evidence-gold-dataset.v1"
GOLD_REVIEW_SCHEMA = "wuli.evidence-gold-review.v1"
EVIDENCE_OVERLAY_SCHEMA = "wuli.evidence-overlay.v1"


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _text(value: Any, field: str, maximum: int = 4000) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    if len(result) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return result


def _ids(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        identifier = _text(item, f"{field}[{index}]", 120)
        if identifier not in result:
            result.append(identifier)
    return result


def normalize_gold_case(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload, "gold_case")
    if raw.get("schema") not in {None, GOLD_CASE_SCHEMA}:
        raise ValueError("gold_case.schema mismatch")
    expected_status = str(raw.get("expected_status") or "").strip()
    if expected_status not in {"sufficient", "insufficient", "not_needed"}:
        raise ValueError("gold_case.expected_status is invalid")
    split = str(raw.get("evaluation_split") or "").strip()
    if split not in {"calibration", "holdout"}:
        raise ValueError("gold_case.evaluation_split is invalid")
    batch_id = str(raw.get("batch_id") or "").strip()
    if split == "holdout" and not batch_id:
        raise ValueError("holdout gold case requires a batch_id")

    need = evidence_contract.normalize_retrieval_need(raw.get("retrieval_need"))
    required = _ids(raw.get("required_evidence_ids"), "gold_case.required_evidence_ids")
    acceptable = _ids(raw.get("acceptable_evidence_ids"), "gold_case.acceptable_evidence_ids")
    forbidden = _ids(raw.get("forbidden_evidence_ids"), "gold_case.forbidden_evidence_ids")
    if set(required) - set(acceptable):
        raise ValueError("required evidence must also be acceptable evidence")
    if set(acceptable) & set(forbidden):
        raise ValueError("acceptable and forbidden evidence must not overlap")
    if expected_status == "sufficient" and not required:
        raise ValueError("sufficient gold case requires required evidence")
    if expected_status in {"sufficient", "insufficient"} and need["criticality"] != "required":
        raise ValueError(f"{expected_status} gold case requires a required need")
    if expected_status == "not_needed" and need["criticality"] != "optional":
        raise ValueError("not_needed gold case requires an optional need")

    return {
        "schema": GOLD_CASE_SCHEMA,
        "case_id": _text(raw.get("case_id"), "gold_case.case_id", 120),
        "question_snapshot_hash": evidence_contract.normalize_fingerprint(
            raw.get("question_snapshot_hash"), "gold_case.question_snapshot_hash"
        ),
        "retrieval_need": need,
        "required_evidence_ids": required,
        "acceptable_evidence_ids": acceptable,
        "forbidden_evidence_ids": forbidden,
        "expected_status": expected_status,
        "teacher_rationale": _text(raw.get("teacher_rationale"), "gold_case.teacher_rationale"),
        "evaluation_split": split,
        "batch_id": batch_id,
    }


def normalize_prediction(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload, "prediction")
    if raw.get("schema") not in {None, PREDICTION_SCHEMA}:
        raise ValueError("prediction.schema mismatch")
    status = str(raw.get("status") or "").strip()
    if status not in evidence_contract.RUN_STATUSES:
        raise ValueError("prediction.status is invalid")
    selected = _ids(raw.get("selected_evidence_ids"), "prediction.selected_evidence_ids")
    candidates = _ids(raw.get("candidate_evidence_ids"), "prediction.candidate_evidence_ids")
    traceable = _ids(raw.get("traceable_evidence_ids"), "prediction.traceable_evidence_ids")
    if set(selected) - set(candidates):
        raise ValueError("selected evidence must come from the candidate pool")
    if set(traceable) - set(selected):
        raise ValueError("traceable evidence must be selected evidence")
    return {
        "schema": PREDICTION_SCHEMA,
        "case_id": _text(raw.get("case_id"), "prediction.case_id", 120),
        "status": status,
        "candidate_evidence_ids": candidates,
        "selected_evidence_ids": selected,
        "traceable_evidence_ids": traceable,
    }


def gold_dataset_fingerprint(payload: dict[str, Any]) -> str:
    material = {
        "dataset_id": payload["dataset_id"],
        "dataset_version": payload["dataset_version"],
        "cases": payload["cases"],
    }
    if "evidence_snapshot_fingerprint" in payload:
        material["evidence_snapshot_fingerprint"] = payload["evidence_snapshot_fingerprint"]
    return evidence_contract.stable_fingerprint(
        "evidence-gold-dataset-v1",
        material,
    )


def normalize_gold_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize a reviewable dataset envelope without inventing teacher approval."""
    raw = _mapping(payload, "gold_dataset")
    if raw.get("schema") not in {None, GOLD_DATASET_SCHEMA}:
        raise ValueError("gold_dataset.schema mismatch")
    review_status = str(raw.get("review_status") or "").strip()
    if review_status not in {"draft", "calibration_frozen", "teacher_approved"}:
        raise ValueError("gold_dataset.review_status is invalid")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ValueError("gold_dataset.cases must be a non-empty array")
    cases = []
    case_ids: set[str] = set()
    for index, item_raw in enumerate(cases_raw):
        item = _mapping(item_raw, f"gold_dataset.cases[{index}]")
        problem = _text(item.get("problem"), f"gold_dataset.cases[{index}].problem", 12000)
        blueprint = _mapping(item.get("blueprint"), f"gold_dataset.cases[{index}].blueprint")
        gold_case = normalize_gold_case(item.get("gold_case"))
        expected_hash = evidence_contract.stable_fingerprint("question-snapshot-v1", problem)
        if gold_case["question_snapshot_hash"] != expected_hash:
            raise ValueError(f"gold_dataset.cases[{index}] question snapshot hash mismatch")
        if gold_case["case_id"] in case_ids:
            raise ValueError("gold dataset case ids must be unique")
        case_ids.add(gold_case["case_id"])
        cases.append({
            "problem": problem,
            "blueprint": blueprint,
            "gold_case": gold_case,
        })
    dataset = {
        "schema": GOLD_DATASET_SCHEMA,
        "dataset_id": _text(raw.get("dataset_id"), "gold_dataset.dataset_id", 120),
        "dataset_version": _text(raw.get("dataset_version"), "gold_dataset.dataset_version", 80),
        "review_status": review_status,
        "label_origin": _text(raw.get("label_origin"), "gold_dataset.label_origin", 80),
        "source_scope": _ids(raw.get("source_scope"), "gold_dataset.source_scope"),
        "reviewer": str(raw.get("reviewer") or "").strip()[:120],
        "reviewed_at": str(raw.get("reviewed_at") or "").strip()[:80],
        "cases": cases,
    }
    if "evidence_snapshot_fingerprint" in raw:
        dataset["evidence_snapshot_fingerprint"] = evidence_contract.normalize_fingerprint(
            raw.get("evidence_snapshot_fingerprint"),
            "gold_dataset.evidence_snapshot_fingerprint",
        )
    if review_status == "teacher_approved" and (not dataset["reviewer"] or not dataset["reviewed_at"]):
        raise ValueError("teacher-approved gold dataset requires reviewer and reviewed_at")
    expected_fingerprint = gold_dataset_fingerprint(dataset)
    supplied = raw.get("dataset_fingerprint")
    if (
        supplied
        and evidence_contract.normalize_fingerprint(supplied, "gold_dataset.dataset_fingerprint")
        != expected_fingerprint
    ):
        raise ValueError("gold_dataset.dataset_fingerprint mismatch")
    dataset["dataset_fingerprint"] = expected_fingerprint
    return dataset


def normalize_gold_review(payload: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload, "gold_review")
    normalized_dataset = normalize_gold_dataset(dataset)
    if raw.get("schema") not in {None, GOLD_REVIEW_SCHEMA}:
        raise ValueError("gold_review.schema mismatch")
    if (
        evidence_contract.normalize_fingerprint(raw.get("dataset_fingerprint"), "gold_review.dataset_fingerprint")
        != normalized_dataset["dataset_fingerprint"]
    ):
        raise ValueError("gold review dataset fingerprint mismatch")
    reviewer = _text(raw.get("reviewer"), "gold_review.reviewer", 120)
    reviewed_at = _text(raw.get("reviewed_at"), "gold_review.reviewed_at", 80)
    decisions_raw = raw.get("decisions")
    if not isinstance(decisions_raw, list):
        raise ValueError("gold_review.decisions must be an array")
    known_ids = {item["gold_case"]["case_id"] for item in normalized_dataset["cases"]}
    decisions = []
    seen: set[str] = set()
    for index, item_raw in enumerate(decisions_raw):
        item = _mapping(item_raw, f"gold_review.decisions[{index}]")
        case_id = _text(item.get("case_id"), f"gold_review.decisions[{index}].case_id", 120)
        if case_id not in known_ids or case_id in seen:
            raise ValueError("gold review must decide each known case exactly once")
        decision = str(item.get("decision") or "").strip()
        if decision not in {"approved", "changes_requested"}:
            raise ValueError("gold review decision is invalid")
        note = str(item.get("note") or "").strip()[:2000]
        if decision == "changes_requested" and not note:
            raise ValueError("changes_requested decision requires a note")
        seen.add(case_id)
        decisions.append({"case_id": case_id, "decision": decision, "note": note})
    if seen != known_ids:
        raise ValueError("gold review must decide each known case exactly once")
    all_approved = all(item["decision"] == "approved" for item in decisions)
    declared_status = str(raw.get("status") or "").strip()
    expected_status = "approved" if all_approved else "changes_requested"
    if declared_status != expected_status:
        raise ValueError("gold review status does not match case decisions")
    return {
        "schema": GOLD_REVIEW_SCHEMA,
        "dataset_id": normalized_dataset["dataset_id"],
        "dataset_fingerprint": normalized_dataset["dataset_fingerprint"],
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "status": expected_status,
        "decisions": decisions,
    }


def apply_gold_review(dataset: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    normalized_dataset = normalize_gold_dataset(dataset)
    normalized_review = normalize_gold_review(review, normalized_dataset)
    if normalized_review["status"] != "approved":
        raise ValueError("gold dataset cannot be approved while changes are requested")
    approved = {
        **normalized_dataset,
        "review_status": "teacher_approved",
        "label_origin": "teacher_reviewed",
        "reviewer": normalized_review["reviewer"],
        "reviewed_at": normalized_review["reviewed_at"],
    }
    return normalize_gold_dataset(approved)


def normalize_evidence_overlay(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize an evaluation-only evidence snapshot and verify its fingerprint."""
    raw = _mapping(payload, "evidence_overlay")
    if raw.get("schema") not in {None, EVIDENCE_OVERLAY_SCHEMA}:
        raise ValueError("evidence_overlay.schema mismatch")
    review_status = str(raw.get("review_status") or "").strip()
    if review_status not in {"draft", "teacher_approved"}:
        raise ValueError("evidence_overlay.review_status is invalid")
    units_raw = raw.get("units")
    if not isinstance(units_raw, list) or not units_raw:
        raise ValueError("evidence_overlay.units must be a non-empty array")
    units = [evidence_contract.normalize_evidence_unit(item) for item in units_raw]
    evidence_ids = [item["evidence_id"] for item in units]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence overlay ids must be unique")
    overlay = {
        "schema": EVIDENCE_OVERLAY_SCHEMA,
        "overlay_id": _text(raw.get("overlay_id"), "evidence_overlay.overlay_id", 120),
        "overlay_fingerprint": evidence_contract.stable_fingerprint("evidence-overlay-v1", units),
        "review_status": review_status,
        "source_scope": _ids(raw.get("source_scope"), "evidence_overlay.source_scope"),
        "units": units,
    }
    supplied = raw.get("overlay_fingerprint")
    if (
        supplied
        and evidence_contract.normalize_fingerprint(supplied, "evidence_overlay.overlay_fingerprint")
        != overlay["overlay_fingerprint"]
    ):
        raise ValueError("evidence_overlay.overlay_fingerprint mismatch")
    if {item["source_kind"] for item in units} - set(overlay["source_scope"]):
        raise ValueError("evidence overlay unit source_kind is outside source_scope")
    return overlay


def apply_gold_review_with_overlay(
    dataset: dict[str, Any],
    review: dict[str, Any],
    overlay: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Approve a dataset, its review receipt, and its evidence snapshot together."""
    normalized_dataset = normalize_gold_dataset(dataset)
    normalized_review = normalize_gold_review(review, normalized_dataset)
    normalized_overlay = normalize_evidence_overlay(overlay)
    if normalized_dataset["dataset_id"] != normalized_overlay["overlay_id"]:
        raise ValueError("gold dataset id does not match evidence overlay id")
    if normalized_dataset.get("evidence_snapshot_fingerprint") != (normalized_overlay["overlay_fingerprint"]):
        raise ValueError("gold dataset evidence snapshot fingerprint mismatch")
    if set(normalized_overlay["source_scope"]) - set(normalized_dataset["source_scope"]):
        raise ValueError("evidence overlay source_scope is outside gold dataset")
    approved_dataset = apply_gold_review(normalized_dataset, normalized_review)
    approved_overlay = {
        **normalized_overlay,
        "review_status": "teacher_approved",
    }
    return approved_dataset, normalized_review, approved_overlay


def prediction_from_run(case_id: str, execution: dict[str, Any]) -> dict[str, Any]:
    run = execution.get("evidence_agent_run", execution)
    if not isinstance(run, dict):
        raise ValueError("evidence execution does not contain a run")
    trace = run.get("retrieval_trace") or []
    candidates = []
    for item in trace:
        if isinstance(item, dict):
            candidates.extend(item.get("candidate_evidence_ids") or [])
    if not candidates and isinstance(execution.get("candidate_pool"), dict):
        for item in execution["candidate_pool"].get("queries", []):
            if isinstance(item, dict):
                candidates.extend(item.get("candidate_evidence_ids") or [])
    selected = [
        item["evidence_id"]
        for item in run.get("evidence_set", [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    return normalize_prediction({
        "case_id": case_id,
        "status": run.get("status"),
        "candidate_evidence_ids": list(dict.fromkeys(candidates)),
        "selected_evidence_ids": selected,
        "traceable_evidence_ids": selected,
    })


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def score_gold_cases(cases: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_cases = [normalize_gold_case(case) for case in cases]
    normalized_predictions = [normalize_prediction(prediction) for prediction in predictions]
    case_by_id = {case["case_id"]: case for case in normalized_cases}
    prediction_by_id = {prediction["case_id"]: prediction for prediction in normalized_predictions}
    if len(case_by_id) != len(normalized_cases):
        raise ValueError("gold case ids must be unique")
    if len(prediction_by_id) != len(normalized_predictions):
        raise ValueError("prediction case ids must be unique")
    if set(prediction_by_id) - set(case_by_id):
        raise ValueError("prediction references an unknown gold case")

    counts = {
        "case_count": len(normalized_cases),
        "prediction_count": len(normalized_predictions),
        "required_evidence_total": 0,
        "required_evidence_candidates": 0,
        "required_evidence_selected": 0,
        "selected_evidence_total": 0,
        "selected_acceptable_total": 0,
        "selected_forbidden_total": 0,
        "candidate_forbidden_total": 0,
        "traceable_selected_total": 0,
        "status_correct": 0,
        "sufficient_case_total": 0,
        "sufficient_case_covered": 0,
    }
    case_results: list[dict[str, Any]] = []
    for case in normalized_cases:
        prediction = prediction_by_id.get(case["case_id"])
        required = set(case["required_evidence_ids"])
        acceptable = set(case["acceptable_evidence_ids"])
        forbidden = set(case["forbidden_evidence_ids"])
        counts["required_evidence_total"] += len(required)
        if prediction is None:
            case_results.append({
                "case_id": case["case_id"],
                "status": "missing-prediction",
                "expected_status": case["expected_status"],
            })
            if case["expected_status"] == "sufficient":
                counts["sufficient_case_total"] += 1
            continue

        candidates = set(prediction["candidate_evidence_ids"])
        selected = set(prediction["selected_evidence_ids"])
        traceable = set(prediction["traceable_evidence_ids"])
        counts["required_evidence_candidates"] += len(required & candidates)
        counts["required_evidence_selected"] += len(required & selected)
        counts["selected_evidence_total"] += len(selected)
        counts["selected_acceptable_total"] += len(selected & acceptable)
        counts["selected_forbidden_total"] += len(selected & forbidden)
        counts["candidate_forbidden_total"] += len(candidates & forbidden)
        counts["traceable_selected_total"] += len(selected & traceable)
        counts["status_correct"] += int(prediction["status"] == case["expected_status"])
        required_covered = required.issubset(selected)
        if case["expected_status"] == "sufficient":
            counts["sufficient_case_total"] += 1
            counts["sufficient_case_covered"] += int(required_covered and prediction["status"] == "sufficient")
        case_results.append({
            "case_id": case["case_id"],
            "status": "scored",
            "expected_status": case["expected_status"],
            "predicted_status": prediction["status"],
            "missing_required_evidence_ids": sorted(required - selected),
            "selected_forbidden_evidence_ids": sorted(selected & forbidden),
            "untraceable_selected_evidence_ids": sorted(selected - traceable),
        })

    metrics = {
        "candidate_required_recall": _ratio(
            counts["required_evidence_candidates"],
            counts["required_evidence_total"],
        ),
        "gold_evidence_retention": _ratio(
            counts["required_evidence_selected"],
            counts["required_evidence_total"],
        ),
        "evidence_precision": _ratio(
            counts["selected_acceptable_total"],
            counts["selected_evidence_total"],
        ),
        "false_friend_admission_count": counts["selected_forbidden_total"],
        "candidate_false_friend_count": counts["candidate_forbidden_total"],
        "traceability_rate": _ratio(
            counts["traceable_selected_total"],
            counts["selected_evidence_total"],
        ),
        "status_accuracy": _ratio(counts["status_correct"], counts["case_count"]),
        "required_need_coverage": _ratio(
            counts["sufficient_case_covered"],
            counts["sufficient_case_total"],
        ),
    }
    gates = {
        "all_cases_scored": counts["case_count"] == counts["prediction_count"],
        "gold_evidence_retention_100": metrics["gold_evidence_retention"] == 1.0,
        "false_friend_zero_admission": metrics["false_friend_admission_count"] == 0,
        "traceability_100": metrics["traceability_rate"] == 1.0,
        "evidence_precision_100": metrics["evidence_precision"] == 1.0,
        "required_need_coverage_100": metrics["required_need_coverage"] == 1.0,
    }
    gates["evidence_set_gate_ready"] = all(gates.values())
    return {
        "schema": "wuli.evidence-gold-report.v1",
        "counts": counts,
        "metrics": metrics,
        "gates": gates,
        "cases": case_results,
    }


def paired_gold_report(
    dataset: dict[str, Any],
    baseline_predictions: list[dict[str, Any]],
    agent_predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_gold_dataset(dataset)
    cases = [item["gold_case"] for item in normalized["cases"]]
    baseline = score_gold_cases(cases, baseline_predictions)
    agent = score_gold_cases(cases, agent_predictions)
    metric_names = (
        "candidate_required_recall",
        "gold_evidence_retention",
        "evidence_precision",
        "false_friend_admission_count",
        "traceability_rate",
        "status_accuracy",
        "required_need_coverage",
    )
    deltas = {key: round(agent["metrics"][key] - baseline["metrics"][key], 4) for key in metric_names}
    splits = {item["gold_case"]["evaluation_split"] for item in normalized["cases"]}
    gates = {
        "dataset_teacher_approved": normalized["review_status"] == "teacher_approved",
        "independent_holdout_present": "holdout" in splits,
        "candidate_recall_non_regression": deltas["candidate_required_recall"] >= 0,
        "gold_retention_non_regression": deltas["gold_evidence_retention"] >= 0,
        "status_accuracy_non_regression": deltas["status_accuracy"] >= 0,
        "false_friend_zero_admission": (agent["metrics"]["false_friend_admission_count"] == 0),
        "traceability_100": agent["metrics"]["traceability_rate"] == 1.0,
        "evidence_precision_100": agent["metrics"]["evidence_precision"] == 1.0,
    }
    gates["eligible_for_production"] = all(gates.values())
    return {
        "schema": "wuli.evidence-paired-gold-report.v1",
        "dataset": {
            "dataset_id": normalized["dataset_id"],
            "dataset_version": normalized["dataset_version"],
            "dataset_fingerprint": normalized["dataset_fingerprint"],
            "review_status": normalized["review_status"],
            "case_count": len(cases),
        },
        "baseline": baseline,
        "evidence_agent": agent,
        "metric_deltas": deltas,
        "gates": gates,
    }
