#!/usr/bin/env python3
"""Freeze and score W3 shadow replay against teacher-reviewed answer truth."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

import w3_pipeline  # noqa: E402

COUNTED_VERDICTS = {"correct", "valid-supplement"}
FINAL_VERDICTS = COUNTED_VERDICTS | {"incorrect"}


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def reviewed_entries(library: Path) -> list[dict[str, Any]]:
    rows = []
    for entry in sorted((library / "entries").iterdir()):
        if not entry.is_dir():
            continue
        record = load_json(entry / "record.json")
        answer = entry / "student-solution.md"
        if record.get("answer_review", {}).get("status") != "passed" or not answer.is_file():
            continue
        rows.append({
            "entry_id": entry.name,
            "title": str(record.get("title", entry.name)),
            "difficulty_score": record.get("difficulty_assessment", {}).get("score"),
            "reference_digest": f"sha256:{digest(answer)}",
        })
    return rows


def historical_case_sources(evals_root: Path, *, current_experiment: Path) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    if not evals_root.is_dir():
        return sources
    current = current_experiment.resolve()
    for manifest_path in sorted(evals_root.glob("*/manifest.json")):
        if manifest_path.parent.resolve() == current:
            continue
        manifest = load_json(manifest_path)
        if manifest.get("kind") != "w3-shadow-benchmark":
            continue
        experiment_name = manifest_path.parent.name
        for case in manifest.get("cases", []):
            entry_id = str(case.get("entry_id", "")).strip()
            if entry_id:
                sources.setdefault(entry_id, []).append(experiment_name)
    return sources


def fresh_readiness(library: Path, experiment: Path, *, holdout_count: int = 5) -> dict[str, Any]:
    rows = reviewed_entries(library)
    history = historical_case_sources(library / "evals", current_experiment=experiment)
    eligible = [item for item in rows if item["entry_id"] not in history]
    return {
        "schema_version": 1,
        "required_case_count": max(5, int(holdout_count)),
        "reviewed_case_count": len(rows),
        "historical_case_count": len(history),
        "fresh_eligible_case_count": len(eligible),
        "replay_candidate_count": len([item for item in rows if item["entry_id"] in history]),
        "missing_case_count": max(0, max(5, int(holdout_count)) - len(eligible)),
        "ready_to_seed": len(eligible) >= max(5, int(holdout_count)),
        "eligible_cases": eligible,
    }


def seed(
    library: Path,
    experiment: Path,
    *,
    holdout_count: int = 5,
    fresh_only: bool = False,
    batch_id: str = "",
    reuse_case_ids: list[str] | None = None,
) -> dict[str, Any]:
    rows = reviewed_entries(library)
    # Stable ordering freezes the split. Harder cases are spread across both
    # cohorts before entry_id provides a deterministic tie-break.
    rows.sort(key=lambda item: (-float(item.get("difficulty_score") or 0), item["entry_id"]))
    holdout_count = max(1, min(int(holdout_count), len(rows)))
    history: dict[str, list[str]] = {}
    reused_ids = list(dict.fromkeys(str(item).strip() for item in (reuse_case_ids or []) if str(item).strip()))
    if fresh_only:
        holdout_count = max(5, int(holdout_count))
        history = historical_case_sources(library / "evals", current_experiment=experiment)
        by_id = {item["entry_id"]: item for item in rows}
        invalid_reused = [entry_id for entry_id in reused_ids if entry_id not in by_id or entry_id not in history]
        if invalid_reused:
            raise ValueError(
                "replay cases must be teacher-reviewed cases from prior W3 manifests: " + ", ".join(invalid_reused)
            )
        if len(reused_ids) > holdout_count:
            raise ValueError("replay case count cannot exceed the requested batch size")
        fresh_rows = [item for item in rows if item["entry_id"] not in history]
        fresh_needed = holdout_count - len(reused_ids)
        if len(fresh_rows) < fresh_needed:
            raise ValueError(
                "W4 test batch is not ready: "
                f"need {fresh_needed} unseen teacher-reviewed cases after "
                f"{len(reused_ids)} explicit replay cases, found {len(fresh_rows)}"
            )
        rows = fresh_rows[:fresh_needed] + [by_id[entry_id] for entry_id in reused_ids]
    for index, row in enumerate(rows):
        if fresh_only and row["entry_id"] in reused_ids:
            row["evaluation_split"] = "replay"
            row["prior_experiments"] = history[row["entry_id"]]
        else:
            row["evaluation_split"] = "holdout" if fresh_only or index < holdout_count else "calibration"
        row["review_status"] = "approved"
    target = experiment / "manifest.json"
    if target.exists():
        raise FileExistsError("manifest already exists; frozen split must not be silently replaced")
    created_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": 2 if fresh_only else 1,
        "kind": "w3-shadow-benchmark",
        "experiment_id": experiment.name,
        "batch_id": batch_id.strip() or experiment.name,
        "created_at": created_at,
        "ground_truth": "teacher-reviewed-student-solution",
        "policy": w3_pipeline.W3_POLICY,
        "split_frozen": True,
        "cases": rows,
        "label_contract": {
            "target_count": "教师复核答案中的独立结论目标数",
            "correct_target_count": "W3 数学正确的目标数；可由 target_judgments 确定性推导",
            "w2_correct_target_count": "当前 W2 与教师真值一致的目标数",
            "target_judgments": (
                "逐目标判定：correct / incorrect / valid-supplement / needs-review；"
                "valid-supplement 仅在教师确认且具有题干约束、复算关系和边界检查时计为正确"
            ),
            "agent_call_count": "W3 实际内部模型调用数",
            "teacher_focus_count": "最终给教师显示的核对卡片数，必须为 0-2",
        },
    }
    if fresh_only:
        manifest["independence"] = {
            "fresh_only": True,
            "prior_experiment_count": len({name for names in history.values() for name in names}),
            "excluded_prior_case_count": len(history),
            "reused_case_count": len(reused_ids),
            "reused_cases_are_holdout": False,
            "selection_rule": "teacher-reviewed-and-absent-from-all-prior-w3-manifests",
            "minimum_case_count": 5,
            "minimum_fresh_case_count_for_production": 5,
            "minimum_target_count": 12,
            "production_rule": (
                "replay cases are regression controls and never count toward independent holdout eligibility"
            ),
        }
        manifest["truth_contract"] = {
            "state": "pending-freeze",
            "rule": "teacher target truth must be approved and locked before any W3 replay",
            "lock_file": "truth-lock.json",
        }
    experiment.mkdir(parents=True, exist_ok=True)
    (experiment / "labels").mkdir(exist_ok=True)
    if fresh_only:
        truth_dir = experiment / "truth"
        truth_dir.mkdir(exist_ok=True)
        for row in rows:
            truth = {
                "schema_version": 1,
                "entry_id": row["entry_id"],
                "status": "draft",
                "reference_digest": row["reference_digest"],
                "targets": [],
            }
            (truth_dir / f"{row['entry_id']}.json").write_text(
                json.dumps(truth, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def freeze_truth(library: Path, experiment: Path) -> dict[str, Any]:
    manifest = load_json(experiment / "manifest.json")
    if manifest.get("schema_version") != 2 or not manifest.get("independence", {}).get("fresh_only"):
        raise ValueError("truth locking is only available for a schema-v2 fresh holdout")
    lock_path = experiment / "truth-lock.json"
    if lock_path.exists():
        raise FileExistsError("truth lock already exists; create a new benchmark version")
    errors: list[str] = []
    locked_cases: list[dict[str, Any]] = []
    total_targets = 0
    for case in manifest.get("cases", []):
        entry_id = str(case.get("entry_id", "")).strip()
        answer = library / "entries" / entry_id / "student-solution.md"
        expected_reference = str(case.get("reference_digest", ""))
        if not answer.is_file() or expected_reference != f"sha256:{digest(answer)}":
            errors.append(f"{entry_id}: teacher truth digest changed")
            continue
        truth_path = experiment / "truth" / f"{entry_id}.json"
        truth = load_json(truth_path)
        if truth.get("status") != "approved":
            errors.append(f"{entry_id}: target truth is not teacher-approved")
            continue
        if truth.get("entry_id") != entry_id or truth.get("reference_digest") != expected_reference:
            errors.append(f"{entry_id}: target truth identity or reference digest mismatch")
            continue
        targets = truth.get("targets")
        if not isinstance(targets, list) or not targets:
            errors.append(f"{entry_id}: target truth must contain at least one target")
            continue
        seen: set[str] = set()
        for target in targets:
            if not isinstance(target, dict):
                errors.append(f"{entry_id}: each truth target must be an object")
                continue
            target_id = str(target.get("target_id", "")).strip()
            conclusion = str(target.get("expected_conclusion", "")).strip()
            source_basis = str(target.get("source_basis", "")).strip()
            if not target_id or target_id in seen:
                errors.append(f"{entry_id}: truth target id is missing or duplicated")
            if not conclusion or not source_basis:
                errors.append(f"{entry_id}: {target_id or 'target'} lacks expected conclusion or source basis")
            seen.add(target_id)
        if any(error.startswith(f"{entry_id}:") for error in errors):
            continue
        total_targets += len(targets)
        locked_cases.append({
            "entry_id": entry_id,
            "reference_digest": expected_reference,
            "truth_digest": f"sha256:{object_digest(truth)}",
            "target_ids": [str(item["target_id"]).strip() for item in targets],
            "target_count": len(targets),
        })
    if len(locked_cases) < 5:
        errors.append("fresh holdout requires at least 5 completely frozen cases")
    if total_targets < 12:
        errors.append("fresh holdout requires at least 12 frozen targets")
    if errors:
        raise ValueError("; ".join(errors))
    lock = {
        "schema_version": 1,
        "kind": "w3-teacher-truth-lock",
        "experiment_id": manifest.get("experiment_id"),
        "batch_id": manifest.get("batch_id"),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "policy": manifest.get("policy"),
        "case_count": len(locked_cases),
        "target_count": total_targets,
        "cases": locked_cases,
    }
    lock["lock_digest"] = f"sha256:{object_digest(lock)}"
    lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return lock


def validate_truth_lock(
    library: Path, experiment: Path, manifest: dict[str, Any]
) -> tuple[dict[str, set[str]], list[str]]:
    if manifest.get("schema_version") != 2:
        return {}, []
    lock = load_json(experiment / "truth-lock.json")
    if not lock:
        return {}, ["fresh holdout: teacher truth must be frozen before scoring"]
    errors: list[str] = []
    target_ids: dict[str, set[str]] = {}
    lock_copy = dict(lock)
    declared_lock_digest = str(lock_copy.pop("lock_digest", ""))
    if declared_lock_digest != f"sha256:{object_digest(lock_copy)}":
        errors.append("fresh holdout: truth lock digest mismatch")
    for case in lock.get("cases", []):
        entry_id = str(case.get("entry_id", "")).strip()
        truth = load_json(experiment / "truth" / f"{entry_id}.json")
        answer = library / "entries" / entry_id / "student-solution.md"
        if str(case.get("truth_digest", "")) != f"sha256:{object_digest(truth)}":
            errors.append(f"{entry_id}: frozen target truth changed")
        if not answer.is_file() or str(case.get("reference_digest", "")) != f"sha256:{digest(answer)}":
            errors.append(f"{entry_id}: frozen teacher answer changed")
        target_ids[entry_id] = {str(item).strip() for item in case.get("target_ids", []) if str(item).strip()}
    manifest_ids = {str(item.get("entry_id", "")).strip() for item in manifest.get("cases", [])}
    if set(target_ids) != manifest_ids:
        errors.append("fresh holdout: truth lock does not cover the frozen manifest")
    return target_ids, errors


def resolved_target_judgments(
    label: dict[str, Any], *, entry_id: str
) -> tuple[int, int, list[dict[str, Any]], list[str]]:
    """Resolve target correctness without treating answer-text equality as truth.

    Schema-v1 labels remain supported. New labels may record a target-level
    ``valid-supplement`` only after the teacher confirms three independent
    checks: prompt constraints, a recomputable relation, and boundary/case
    coverage. ``needs-review`` never silently enters a scored cohort.
    """
    target_count = int(label.get("target_count", 0))
    raw = label.get("target_judgments")
    if raw is None:
        return int(label.get("correct_target_count", 0)), 0, [], []
    if not isinstance(raw, list):
        return 0, 0, [], [f"{entry_id}: target_judgments must be a list"]
    errors: list[str] = []
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            errors.append(f"{entry_id}: target judgment must be an object")
            continue
        target_id = str(item.get("target_id", "")).strip()
        verdict = str(item.get("verdict", "")).strip()
        if not target_id or target_id in seen:
            errors.append(f"{entry_id}: target judgment id is missing or duplicated")
            continue
        seen.add(target_id)
        if verdict not in FINAL_VERDICTS | {"needs-review"}:
            errors.append(f"{entry_id}: invalid target verdict for {target_id}")
            continue
        if verdict == "needs-review":
            errors.append(f"{entry_id}: {target_id} still needs teacher review")
        if verdict == "valid-supplement":
            checks = item.get("validation", {})
            required = ("prompt_constraints", "recomputable_relation", "boundary_cases")
            if (
                item.get("teacher_confirmed") is not True
                or not isinstance(checks, dict)
                or any(not str(checks.get(key, "")).strip() for key in required)
            ):
                errors.append(
                    f"{entry_id}: {target_id} valid-supplement lacks teacher confirmation "
                    "or three-part deterministic validation"
                )
        normalized.append({
            "target_id": target_id,
            "verdict": verdict,
            "teacher_confirmed": item.get("teacher_confirmed") is True,
        })
    if len(seen) != target_count:
        errors.append(f"{entry_id}: target_judgments must cover every target exactly once")
    correct = sum(1 for item in normalized if item["verdict"] in COUNTED_VERDICTS)
    supplements = sum(1 for item in normalized if item["verdict"] == "valid-supplement")
    declared = label.get("correct_target_count")
    if declared is not None and int(declared) != correct:
        errors.append(f"{entry_id}: correct_target_count disagrees with target_judgments")
    return correct, supplements, normalized, errors


def resolved_w2_target_judgments(
    label: dict[str, Any], *, expected_target_ids: set[str], entry_id: str
) -> tuple[int | None, list[dict[str, str]], list[str]]:
    raw = label.get("w2_target_judgments")
    if raw is None:
        return None, [], []
    if not isinstance(raw, list):
        return None, [], [f"{entry_id}: w2_target_judgments must be a list"]
    errors: list[str] = []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            errors.append(f"{entry_id}: W2 target judgment must be an object")
            continue
        target_id = str(item.get("target_id", "")).strip()
        verdict = str(item.get("verdict", "")).strip()
        if not target_id or target_id in seen:
            errors.append(f"{entry_id}: W2 target judgment id is missing or duplicated")
            continue
        if verdict not in {"correct", "incorrect"}:
            errors.append(f"{entry_id}: invalid W2 target verdict for {target_id}")
            continue
        seen.add(target_id)
        normalized.append({"target_id": target_id, "verdict": verdict})
    if seen != expected_target_ids:
        errors.append(f"{entry_id}: W2 judgments do not match frozen target ids")
    correct = sum(item["verdict"] == "correct" for item in normalized)
    declared = label.get("w2_correct_target_count")
    if declared is not None and int(declared) != correct:
        errors.append(f"{entry_id}: W2 correct count disagrees with target judgments")
    return correct, normalized, errors


def collect(library: Path, experiment: Path) -> tuple[list[dict[str, Any]], list[str]]:
    manifest = load_json(experiment / "manifest.json")
    rows: list[dict[str, Any]] = []
    locked_targets, errors = validate_truth_lock(library, experiment, manifest)
    for case in manifest.get("cases", []):
        entry_id = str(case.get("entry_id", ""))
        entry = library / "entries" / entry_id
        answer = entry / "student-solution.md"
        expected_digest = str(case.get("reference_digest", ""))
        if not answer.is_file() or expected_digest != f"sha256:{digest(answer)}":
            errors.append(f"{entry_id}: teacher truth digest changed; refresh requires a new benchmark version")
            continue
        label = load_json(experiment / "labels" / f"{entry_id}.json")
        shadow = load_json(entry / "w3-shadow-report.json")
        if not label:
            errors.append(f"{entry_id}: missing teacher target label")
            continue
        if shadow.get("status") != "completed":
            errors.append(f"{entry_id}: missing completed W3 shadow report")
            continue
        report = shadow.get("report", {})
        metrics = report.get("metrics", {}) if isinstance(report, dict) else {}
        correct, supplements, judgments, judgment_errors = resolved_target_judgments(label, entry_id=entry_id)
        errors.extend(judgment_errors)
        if manifest.get("schema_version") == 2:
            judgment_ids = {str(item.get("target_id", "")).strip() for item in judgments}
            if judgment_ids != locked_targets.get(entry_id, set()):
                errors.append(f"{entry_id}: scored target ids do not match frozen teacher truth")
        w2_correct, w2_judgments, w2_errors = resolved_w2_target_judgments(
            label,
            expected_target_ids=locked_targets.get(entry_id, set()),
            entry_id=entry_id,
        )
        errors.extend(w2_errors)
        row = {
            **case,
            "target_count": int(label.get("target_count", 0)),
            "correct_target_count": correct,
            "w2_correct_target_count": (
                w2_correct if manifest.get("schema_version") == 2 else int(label.get("w2_correct_target_count", 0))
            ),
            "w2_target_judgments": w2_judgments,
            "w2_quality_warnings": [
                str(item)[:500] for item in label.get("w2_quality_warnings", []) if str(item).strip()
            ],
            "validated_supplement_target_count": supplements,
            "target_judgments": judgments,
            "reference_revised_after_shadow": bool(label.get("reference_revision")),
            "agent_call_count": len(shadow.get("stages", [])),
            "teacher_focus_count": int(metrics.get("teacher_focus_count", 0)),
        }
        if row["target_count"] <= 0:
            errors.append(f"{entry_id}: target_count must be positive")
            continue
        if not 0 <= row["correct_target_count"] <= row["target_count"]:
            errors.append(f"{entry_id}: W3 correctness count is invalid")
            continue
        if (
            row["w2_correct_target_count"] is not None
            and not 0 <= row["w2_correct_target_count"] <= row["target_count"]
        ):
            errors.append(f"{entry_id}: W2 correctness count is invalid")
            continue
        if not 0 <= row["teacher_focus_count"] <= 2:
            errors.append(f"{entry_id}: teacher focus exceeds the two-card budget")
            continue
        rows.append(row)
    return rows, errors


def paired_score(library: Path, experiment: Path) -> dict[str, Any]:
    manifest = load_json(experiment / "manifest.json")
    rows, collection_errors = collect(library, experiment)
    by_id = {str(item.get("entry_id", "")): item for item in rows}
    pairs: list[dict[str, Any]] = []
    errors = list(collection_errors)
    for case in manifest.get("cases", []):
        entry_id = str(case.get("entry_id", "")).strip()
        row = by_id.get(entry_id)
        if not row or row.get("w2_correct_target_count") is None:
            continue
        artifact_dir = experiment / "artifacts" / entry_id
        w2_candidate = artifact_dir / "web-candidate.md"
        w2_meta = load_json(artifact_dir / "web-candidate.meta.json")
        shadow = load_json(library / "entries" / entry_id / "w3-shadow-report.json")
        if not w2_candidate.is_file():
            errors.append(f"{entry_id}: missing generated W2 candidate")
            continue
        if (
            w2_meta.get("source") != "teacher-console-browser-click"
            or w2_meta.get("status") != "completed"
            or w2_meta.get("evidence_mode") != "candidate"
        ):
            errors.append(f"{entry_id}: W2 candidate provenance is not benchmark-ready")
            continue
        report = shadow.get("report", {}) if isinstance(shadow.get("report"), dict) else {}
        if shadow.get("status") != "completed" or report.get("policy") != manifest.get("policy"):
            errors.append(f"{entry_id}: W3 shadow provenance is not benchmark-ready")
            continue
        same_model = str(w2_meta.get("model_id", "")) == str(shadow.get("model_id", ""))
        same_tier = str(w2_meta.get("routing_tier", "")) == str(shadow.get("routing_tier", ""))
        if not same_model or not same_tier:
            errors.append(f"{entry_id}: W2/W3 model or routing tier mismatch")
            continue
        target_count = int(row.get("target_count", 0))
        w2_correct = int(row.get("w2_correct_target_count", 0))
        w3_correct = int(row.get("correct_target_count", 0))
        quality_warnings = list(row.get("w2_quality_warnings", []))
        pairs.append({
            "entry_id": entry_id,
            "evaluation_split": case.get("evaluation_split"),
            "target_count": target_count,
            "w2_correct_target_count": w2_correct,
            "w3_correct_target_count": w3_correct,
            "w2_target_accuracy": round(w2_correct / target_count, 4),
            "w3_target_accuracy": round(w3_correct / target_count, 4),
            "model_id": w2_meta.get("model_id"),
            "routing_tier": w2_meta.get("routing_tier"),
            "evidence_mode": w2_meta.get("evidence_mode"),
            "evidence_snapshot_sha256": w2_meta.get("evidence_snapshot_sha256"),
            "w3_policy": report.get("policy"),
            "w3_agent_call_count": len(shadow.get("stages", [])),
            "teacher_focus_count": int(report.get("metrics", {}).get("teacher_focus_count", 0)),
            "w2_delivery_quality_pass": not quality_warnings,
            "w2_quality_warnings": quality_warnings,
        })
    total_targets = sum(item["target_count"] for item in pairs)
    w2_correct = sum(item["w2_correct_target_count"] for item in pairs)
    w3_correct = sum(item["w3_correct_target_count"] for item in pairs)
    report = {
        "schema_version": 1,
        "kind": "w4-w2-w3-paired-benchmark",
        "policy": manifest.get("policy"),
        "pair_count": len(pairs),
        "target_count": total_targets,
        "w2_target_accuracy": (round(w2_correct / total_targets, 4) if total_targets else None),
        "w3_target_accuracy": (round(w3_correct / total_targets, 4) if total_targets else None),
        "w3_accuracy_delta": (round((w3_correct - w2_correct) / total_targets, 4) if total_targets else None),
        "w2_delivery_quality_pass_count": sum(item["w2_delivery_quality_pass"] for item in pairs),
        "comparison_ready": bool(pairs and not errors),
        "production_evidence": bool(
            pairs and not errors and all(item["evaluation_split"] == "holdout" for item in pairs)
        ),
        "pairs": pairs,
        "errors": errors,
    }
    (experiment / "paired-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def score(library: Path, experiment: Path) -> dict[str, Any]:
    rows, errors = collect(library, experiment)
    metrics = w3_pipeline.acceptance_metrics(rows)
    manifest = load_json(experiment / "manifest.json")
    holdout_ids = {
        str(item.get("entry_id", "")) for item in manifest.get("cases", []) if item.get("evaluation_split") == "holdout"
    }
    schema_v2_ids = (
        {str(item.get("entry_id", "")) for item in manifest.get("cases", [])}
        if manifest.get("schema_version") == 2
        else set()
    )
    blocking_ids = schema_v2_ids or holdout_ids
    blocking_errors = [
        error for error in errors if any(entry_id and error.startswith(entry_id) for entry_id in blocking_ids)
    ]
    blocking_errors.extend(
        error for error in errors if error.startswith("fresh holdout:") and error not in blocking_errors
    )
    if blocking_errors:
        metrics["gates"]["production_eligible"] = False
        if manifest.get("schema_version") == 2:
            metrics["gates"]["independent_holdout_intact"] = False
            metrics["gates"]["fresh_holdout_required"] = True
    metrics["validation"] = {
        "status": "ready" if not blocking_errors else "incomplete",
        "scored_case_count": len(rows),
        "blocking_errors": blocking_errors,
        "calibration_warnings": [error for error in errors if error not in blocking_errors],
    }
    (experiment / "result.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=PROJECT_ROOT / "student-error-library")
    parser.add_argument(
        "--experiment",
        type=Path,
        default=PROJECT_ROOT / "student-error-library" / "evals" / "w3-shadow-v1",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    seed_parser = sub.add_parser("seed")
    seed_parser.add_argument("--holdout-count", type=int, default=5)
    seed_parser.add_argument("--fresh-only", action="store_true")
    seed_parser.add_argument("--batch-id", default="")
    seed_parser.add_argument(
        "--reuse-case",
        action="append",
        default=[],
        help="Explicit prior W3 case to include as replay control; repeatable.",
    )
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--holdout-count", type=int, default=5)
    sub.add_parser("freeze-truth")
    sub.add_parser("score")
    sub.add_parser("paired-score")
    args = parser.parse_args()
    try:
        if args.command == "seed":
            result = seed(
                args.library,
                args.experiment,
                holdout_count=args.holdout_count,
                fresh_only=args.fresh_only,
                batch_id=args.batch_id,
                reuse_case_ids=args.reuse_case,
            )
        elif args.command == "status":
            result = fresh_readiness(args.library, args.experiment, holdout_count=args.holdout_count)
        elif args.command == "freeze-truth":
            result = freeze_truth(args.library, args.experiment)
        elif args.command == "score":
            result = score(args.library, args.experiment)
        else:
            result = paired_score(args.library, args.experiment)
    except (FileExistsError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
