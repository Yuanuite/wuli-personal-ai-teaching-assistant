"""Frozen contracts for the Wuli Evidence Agent shadow pipeline.

This module deliberately has no dependency on the production retrieval or W3
pipeline.  Phase A uses it to validate fixtures, shadow artifacts, and future
``evidence.build`` candidates without changing current runtime behaviour.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath
from typing import Any, cast

RETRIEVAL_NEED_SCHEMA = "wuli.retrieval-need.v1"
EVIDENCE_UNIT_SCHEMA = "wuli.evidence-unit.v1"
EVIDENCE_USAGE_LEDGER_SCHEMA = "wuli.evidence-usage-ledger.v1"
EVIDENCE_AGENT_RUN_SCHEMA = "wuli.evidence-agent-run.v1"

PURPOSES = {
    "method_candidate",
    "applicability_check",
    "exception_check",
    "boundary_check",
    "verification_support",
    "false_friend_check",
}
CRITICALITIES = {"required", "optional"}
AUTHORITY_LEVELS = {"A", "B", "C", "D", "N"}
AUTHORITY_RANK = {"A": 4, "B": 3, "C": 2, "D": 1, "N": 0}
UNIT_KINDS = {
    "secondary_conclusion",
    "method_applicability",
    "false_friend_warning",
    "verification_rule",
}
SOURCE_AUTHORITIES = {
    "curated_technique": "A",
    "deterministic_formula": "A",
    "approved_solution": "B",
    "approved_physics_model": "B",
    "teacher_feedback": "C",
    "evaluator_warning": "C",
    "agent_candidate": "D",
    "routing_summary": "N",
}
USAGE_KINDS = {
    "navigation_only",
    "method_hint",
    "condition_warning",
    "false_friend_warning",
    "secondary_conclusion_candidate",
    "citation_support",
}
RUN_STATUSES = {"sufficient", "insufficient", "not_needed", "unavailable"}
NEED_STATUSES = {"covered", "missing", "conflicted", "not_required"}
FUSION_POLICIES = {
    "single-route-bypass",
    "weighted-rrf-shadow",
    "weighted-rrf-production",
}
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def stable_fingerprint(namespace: str, payload: Any) -> str:
    canonical = json.dumps(
        {"namespace": namespace, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _text(value: Any, field: str, maximum: int = 4000) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be non-empty")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return text


def _texts(
    value: Any,
    field: str,
    *,
    required: bool = False,
    maximum_items: int = 32,
    maximum_text: int = 500,
) -> list[str]:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _text(item, f"{field}[{index}]", maximum_text)
        if text not in result:
            result.append(text)
    if required and not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) > maximum_items:
        raise ValueError(f"{field} exceeds {maximum_items} items")
    return result


def _enum(value: Any, field: str, allowed: set[str]) -> str:
    normalized = str(value or "").strip()
    if normalized not in allowed:
        raise ValueError(f"{field} must be one of {sorted(allowed)}")
    return normalized


def _hash(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not HASH_RE.fullmatch(normalized):
        raise ValueError(f"{field} must be a sha256 fingerprint")
    return normalized


def normalize_fingerprint(value: Any, field: str = "fingerprint") -> str:
    return _hash(value, field)


def _relative_path(value: Any, field: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a safe relative path")
    return normalized


def normalize_retrieval_need(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload, "retrieval_need")
    if raw.get("schema") not in {None, RETRIEVAL_NEED_SCHEMA}:
        raise ValueError("retrieval_need.schema mismatch")
    need = {
        "schema": RETRIEVAL_NEED_SCHEMA,
        "need_id": _text(raw.get("need_id"), "retrieval_need.need_id", 80),
        "purpose": _enum(raw.get("purpose"), "retrieval_need.purpose", PURPOSES),
        "question": _text(raw.get("question"), "retrieval_need.question", 1000),
        "required_facets": _texts(
            raw.get("required_facets"),
            "retrieval_need.required_facets",
            required=True,
            maximum_items=16,
        ),
        "forbidden_conflicts": _texts(
            raw.get("forbidden_conflicts"),
            "retrieval_need.forbidden_conflicts",
            maximum_items=16,
        ),
        "minimum_authority": _enum(
            raw.get("minimum_authority"),
            "retrieval_need.minimum_authority",
            AUTHORITY_LEVELS - {"N"},
        ),
        "criticality": _enum(raw.get("criticality"), "retrieval_need.criticality", CRITICALITIES),
        "target_ids": _texts(raw.get("target_ids"), "retrieval_need.target_ids", maximum_items=16),
        "stage_ids": _texts(raw.get("stage_ids"), "retrieval_need.stage_ids", maximum_items=16),
        "obligation_ids": _texts(
            raw.get("obligation_ids"),
            "retrieval_need.obligation_ids",
            maximum_items=16,
        ),
    }
    # This field is intentionally optional and omitted from legacy payloads so
    # historical dataset fingerprints remain stable.  It names an erroneous
    # behaviour the evidence is supposed to diagnose, not a condition that
    # makes the evidence inapplicable.
    if "diagnostic_targets" in raw:
        need["diagnostic_targets"] = _texts(
            raw.get("diagnostic_targets"),
            "retrieval_need.diagnostic_targets",
            maximum_items=16,
            maximum_text=500,
        )
    return need


def _normalize_locator(payload: Any) -> dict[str, Any]:
    raw = _mapping(payload, "evidence_unit.source_locator")
    locator: dict[str, Any] = {
        "path": _relative_path(raw.get("path"), "evidence_unit.source_locator.path"),
        "section": _text(raw.get("section"), "evidence_unit.source_locator.section", 500),
    }
    start = raw.get("start_line")
    end = raw.get("end_line")
    if isinstance(start, bool) or not isinstance(start, int) or start < 1:
        raise ValueError("evidence_unit.source_locator.start_line must be positive")
    if isinstance(end, bool) or not isinstance(end, int) or end < start:
        raise ValueError("evidence_unit.source_locator.end_line must not precede start_line")
    locator["start_line"] = start
    locator["end_line"] = end
    return locator


def evidence_unit_content_hash(payload: dict[str, Any]) -> str:
    material = {
        key: payload[key]
        for key in (
            "unit_kind",
            "source_kind",
            "source_locator",
            "text",
            "physics_facets",
            "applicability",
            "exceptions",
            "authority_level",
        )
    }
    return stable_fingerprint("evidence-unit-content-v1", material)


def normalize_evidence_unit(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload, "evidence_unit")
    if raw.get("schema") not in {None, EVIDENCE_UNIT_SCHEMA}:
        raise ValueError("evidence_unit.schema mismatch")
    source_kind = _enum(
        raw.get("source_kind"),
        "evidence_unit.source_kind",
        set(SOURCE_AUTHORITIES),
    )
    authority = _enum(
        raw.get("authority_level"),
        "evidence_unit.authority_level",
        AUTHORITY_LEVELS,
    )
    if authority != SOURCE_AUTHORITIES[source_kind]:
        raise ValueError("evidence_unit.authority_level does not match source_kind policy")
    unit = {
        "schema": EVIDENCE_UNIT_SCHEMA,
        "evidence_id": _text(raw.get("evidence_id"), "evidence_unit.evidence_id", 120),
        "unit_kind": _enum(raw.get("unit_kind"), "evidence_unit.unit_kind", UNIT_KINDS),
        "source_kind": source_kind,
        "source_locator": _normalize_locator(raw.get("source_locator")),
        "text": _text(raw.get("text"), "evidence_unit.text", 12000),
        "physics_facets": _texts(
            raw.get("physics_facets"),
            "evidence_unit.physics_facets",
            required=True,
            maximum_items=32,
        ),
        "applicability": _texts(
            raw.get("applicability"),
            "evidence_unit.applicability",
            required=True,
            maximum_items=32,
            maximum_text=1000,
        ),
        "exceptions": _texts(
            raw.get("exceptions"),
            "evidence_unit.exceptions",
            maximum_items=32,
            maximum_text=1000,
        ),
        "authority_level": authority,
    }
    expected_hash = evidence_unit_content_hash(unit)
    supplied_hash = raw.get("content_hash")
    if supplied_hash is not None and _hash(supplied_hash, "evidence_unit.content_hash") != expected_hash:
        raise ValueError("evidence_unit.content_hash does not match canonical content")
    unit["content_hash"] = expected_hash
    return unit


def normalize_usage_ledger(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload, "evidence_usage_ledger")
    if raw.get("schema") not in {None, EVIDENCE_USAGE_LEDGER_SCHEMA}:
        raise ValueError("evidence_usage_ledger.schema mismatch")
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise ValueError("evidence_usage_ledger.entries must be an array")
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item_raw in enumerate(entries_raw):
        item = _mapping(item_raw, f"evidence_usage_ledger.entries[{index}]")
        entry = {
            "evidence_id": _text(
                item.get("evidence_id"),
                f"evidence_usage_ledger.entries[{index}].evidence_id",
                120,
            ),
            "need_ids": _texts(
                item.get("need_ids"),
                f"evidence_usage_ledger.entries[{index}].need_ids",
                required=True,
                maximum_items=16,
            ),
            "usage": _enum(
                item.get("usage"),
                f"evidence_usage_ledger.entries[{index}].usage",
                USAGE_KINDS,
            ),
            "influenced_claim_ids": _texts(
                item.get("influenced_claim_ids"),
                f"evidence_usage_ledger.entries[{index}].influenced_claim_ids",
                maximum_items=32,
            ),
            "verification_ids": _texts(
                item.get("verification_ids"),
                f"evidence_usage_ledger.entries[{index}].verification_ids",
                maximum_items=32,
            ),
        }
        key = (entry["evidence_id"], entry["usage"])
        if key in seen:
            raise ValueError("evidence_usage_ledger contains a duplicate usage entry")
        seen.add(cast(tuple[str, str], key))
        if entry["usage"] == "navigation_only" and entry["influenced_claim_ids"]:
            raise ValueError("navigation_only evidence cannot influence a claim")
        if entry["influenced_claim_ids"] and not entry["verification_ids"]:
            raise ValueError("claim-influencing evidence must bind current-problem verification")
        entries.append(entry)
    return {"schema": EVIDENCE_USAGE_LEDGER_SCHEMA, "entries": entries}


def _normalize_coverage(payload: Any, index: int) -> dict[str, Any]:
    raw = _mapping(payload, f"coverage[{index}]")
    status = _enum(raw.get("status"), f"coverage[{index}].status", NEED_STATUSES)
    evidence_ids = _texts(raw.get("evidence_ids"), f"coverage[{index}].evidence_ids", maximum_items=32)
    covered = _texts(
        raw.get("covered_facets"),
        f"coverage[{index}].covered_facets",
        maximum_items=32,
    )
    missing = _texts(
        raw.get("missing_facets"),
        f"coverage[{index}].missing_facets",
        maximum_items=32,
    )
    conflicts = _texts(
        raw.get("hard_conflicts"),
        f"coverage[{index}].hard_conflicts",
        maximum_items=32,
        maximum_text=1000,
    )
    if status == "covered" and (not evidence_ids or missing or conflicts):
        raise ValueError("covered need must bind evidence and have no missing facets or conflicts")
    if status == "conflicted" and not conflicts:
        raise ValueError("conflicted need must describe a hard conflict")
    return {
        "need_id": _text(raw.get("need_id"), f"coverage[{index}].need_id", 80),
        "status": status,
        "evidence_ids": evidence_ids,
        "covered_facets": covered,
        "missing_facets": missing,
        "hard_conflicts": conflicts,
    }


def normalize_evidence_agent_run(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload, "evidence_agent_run")
    if raw.get("schema") not in {None, EVIDENCE_AGENT_RUN_SCHEMA}:
        raise ValueError("evidence_agent_run.schema mismatch")
    needs_raw = raw.get("retrieval_needs")
    units_raw = raw.get("evidence_set")
    coverage_raw = raw.get("coverage")
    trace_raw = raw.get("retrieval_trace")
    if not isinstance(needs_raw, list):
        raise ValueError("evidence_agent_run.retrieval_needs must be an array")
    if not isinstance(units_raw, list):
        raise ValueError("evidence_agent_run.evidence_set must be an array")
    if not isinstance(coverage_raw, list):
        raise ValueError("evidence_agent_run.coverage must be an array")
    if not isinstance(trace_raw, list):
        raise ValueError("evidence_agent_run.retrieval_trace must be an array")

    needs = [normalize_retrieval_need(item) for item in needs_raw]
    units = [normalize_evidence_unit(item) for item in units_raw]
    coverage = [_normalize_coverage(item, index) for index, item in enumerate(coverage_raw)]
    ledger = normalize_usage_ledger(raw.get("usage_ledger") or {"entries": []})
    status = _enum(raw.get("status"), "evidence_agent_run.status", RUN_STATUSES)

    need_by_id = {item["need_id"]: item for item in needs}
    if len(need_by_id) != len(needs):
        raise ValueError("retrieval need ids must be unique")
    unit_by_id = {item["evidence_id"]: item for item in units}
    if len(unit_by_id) != len(units):
        raise ValueError("evidence ids must be unique")
    if any(item["source_kind"] == "routing_summary" for item in units):
        raise ValueError("routing summaries may navigate but cannot enter evidence_set")
    coverage_by_id = {item["need_id"]: item for item in coverage}
    if len(coverage_by_id) != len(coverage):
        raise ValueError("coverage need ids must be unique")
    if set(coverage_by_id) != set(need_by_id):
        raise ValueError("coverage must contain exactly one row per retrieval need")

    for row in coverage:
        if set(row["evidence_ids"]) - set(unit_by_id):
            raise ValueError("coverage references unknown evidence")
        required_facets = set(need_by_id[row["need_id"]]["required_facets"])
        if row["status"] == "covered" and not required_facets.issubset(set(row["covered_facets"])):
            raise ValueError("covered need must cover every required facet")
        if row["status"] == "covered":
            minimum = need_by_id[row["need_id"]]["minimum_authority"]
            if any(
                AUTHORITY_RANK[unit_by_id[evidence_id]["authority_level"]] < AUTHORITY_RANK[minimum]
                for evidence_id in row["evidence_ids"]
            ):
                raise ValueError("covered need binds evidence below minimum authority")

    for entry in ledger["entries"]:
        if entry["evidence_id"] not in unit_by_id:
            raise ValueError("usage ledger references unknown evidence")
        if set(entry["need_ids"]) - set(need_by_id):
            raise ValueError("usage ledger references unknown retrieval need")

    required_rows = [coverage_by_id[item["need_id"]] for item in needs if item["criticality"] == "required"]
    all_required_covered = bool(required_rows) and all(item["status"] == "covered" for item in required_rows)
    has_required_gap = any(item["status"] in {"missing", "conflicted"} for item in required_rows)
    if status == "sufficient" and not all_required_covered:
        raise ValueError("sufficient requires every required need to be covered")
    if status == "insufficient" and not has_required_gap:
        raise ValueError("insufficient requires a missing or conflicted required need")
    if status == "not_needed" and required_rows:
        raise ValueError("not_needed cannot contain a required retrieval need")
    if status in {"not_needed", "unavailable"} and units:
        raise ValueError(f"{status} run must have an empty evidence set")

    insufficient = raw.get("insufficient_evidence")
    if status in {"insufficient", "unavailable"}:
        insufficient = _text(insufficient, "evidence_agent_run.insufficient_evidence", 2000)
    elif insufficient not in {None, ""}:
        raise ValueError("insufficient_evidence is only allowed for insufficient or unavailable")
    else:
        insufficient = None

    trace: list[dict[str, Any]] = []
    for index, item_raw in enumerate(trace_raw):
        item = _mapping(item_raw, f"retrieval_trace[{index}]")
        round_number = item.get("round")
        if isinstance(round_number, bool) or not isinstance(round_number, int) or round_number not in {1, 2}:
            raise ValueError("retrieval trace round must be 1 or 2")
        trace.append({
            "round": round_number,
            "query_ids": _texts(
                item.get("query_ids"),
                f"retrieval_trace[{index}].query_ids",
                maximum_items=16,
            ),
            "candidate_evidence_ids": _texts(
                item.get("candidate_evidence_ids"),
                f"retrieval_trace[{index}].candidate_evidence_ids",
                maximum_items=128,
            ),
            "newly_covered_need_ids": _texts(
                item.get("newly_covered_need_ids"),
                f"retrieval_trace[{index}].newly_covered_need_ids",
                maximum_items=16,
            ),
            "stop_reason": _text(
                item.get("stop_reason"),
                f"retrieval_trace[{index}].stop_reason",
                500,
            ),
        })
    if len(trace) > 2:
        raise ValueError("evidence_agent_run supports at most two retrieval rounds")
    if [item["round"] for item in trace] != sorted({item["round"] for item in trace}):
        raise ValueError("retrieval trace rounds must be unique and ordered")

    run = {
        "schema": EVIDENCE_AGENT_RUN_SCHEMA,
        "task_fingerprint": _hash(raw.get("task_fingerprint"), "evidence_agent_run.task_fingerprint"),
        "question_snapshot_hash": _hash(
            raw.get("question_snapshot_hash"),
            "evidence_agent_run.question_snapshot_hash",
        ),
        "blueprint_fingerprint": _hash(
            raw.get("blueprint_fingerprint"),
            "evidence_agent_run.blueprint_fingerprint",
        ),
        "knowledge_store_fingerprint": _hash(
            raw.get("knowledge_store_fingerprint"),
            "evidence_agent_run.knowledge_store_fingerprint",
        ),
        "fusion_policy": _enum(
            raw.get("fusion_policy"),
            "evidence_agent_run.fusion_policy",
            FUSION_POLICIES,
        ),
        "status": status,
        "retrieval_needs": needs,
        "evidence_set": units,
        "coverage": coverage,
        "usage_ledger": ledger,
        "retrieval_trace": trace,
        "insufficient_evidence": insufficient,
    }
    expected_task_fingerprint = stable_fingerprint(
        "evidence-build-task-v1",
        {
            "question_snapshot_hash": run["question_snapshot_hash"],
            "blueprint_fingerprint": run["blueprint_fingerprint"],
            "knowledge_store_fingerprint": run["knowledge_store_fingerprint"],
            "retrieval_needs": run["retrieval_needs"],
            "fusion_policy": run["fusion_policy"],
        },
    )
    if run["task_fingerprint"] != expected_task_fingerprint:
        raise ValueError("evidence_agent_run.task_fingerprint mismatch")
    return run


def evidence_build_task_fingerprint(
    *,
    question_snapshot_hash: str,
    blueprint_fingerprint: str,
    knowledge_store_fingerprint: str,
    retrieval_needs: list[dict[str, Any]],
    fusion_policy: str,
) -> str:
    needs = [normalize_retrieval_need(item) for item in retrieval_needs]
    policy = _enum(fusion_policy, "fusion_policy", FUSION_POLICIES)
    return stable_fingerprint(
        "evidence-build-task-v1",
        {
            "question_snapshot_hash": _hash(question_snapshot_hash, "question_snapshot_hash"),
            "blueprint_fingerprint": _hash(blueprint_fingerprint, "blueprint_fingerprint"),
            "knowledge_store_fingerprint": _hash(knowledge_store_fingerprint, "knowledge_store_fingerprint"),
            "retrieval_needs": needs,
            "fusion_policy": policy,
        },
    )
