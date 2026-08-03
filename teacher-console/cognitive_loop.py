#!/usr/bin/env python3
"""Bounded control-plane primitives for challenge, rollback, and exploration."""

from __future__ import annotations

import json
import random
import re
import unicodedata
from typing import Any

import claim_ledger
import correctness_policy
import structured_text
from log import get_logger

logger = get_logger("cognitive_loop")

_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,79}$")
_DIRECTION_KEY_PATTERN = re.compile(r"^[+-]?[A-Za-z][A-Za-z0-9._-]{0,31}$")
_INTERFACE_FIELDS = {
    "stage_id",
    "coordinate_frame",
    "time_origin",
    "directions",
    "entry_state",
    "exit_state",
    "required_entry_keys",
    "carried_state_keys",
}
_TRANSITION_FIELDS = {
    "from_stage",
    "to_stage",
    "event",
    "state_mapping",
    "introduced_entry_keys",
    "coordinate_transform",
    "time_transform",
    "direction_transform",
}
_MAPPING_FIELDS = {"from_key", "to_key", "transform"}
_CHALLENGE_FIELDS = {
    "id",
    "snapshot_version",
    "trigger",
    "claim_ids",
    "specific_doubt",
    "falsification_test",
    "suggested_backjump",
    "status",
}
CHALLENGE_TRIGGERS = (
    "verification-conflict",
    "interface-mismatch",
    "missing-coverage",
    "risk-audit",
)
CHALLENGE_STATUSES = (
    "candidate",
    "open",
    "resolved",
    "rejected",
    "superseded",
)
_VAGUE_CHALLENGE_TEXT = {
    "check",
    "检查",
    "检查一下",
    "可能有错",
    "不确定",
    "再想想",
}
_CONFLICT_EVIDENCE_FIELDS = {
    "interface_issue_codes",
    "event_order_claim_ids",
    "missing_obligation_ids",
    "missing_target_ids",
    "conflicting_claim_ids",
    "component_failure_claim_ids",
}
CONFLICT_CLASSES = (
    "order",
    "interface",
    "component",
    "omitted-condition",
    "decomposition",
)
_PROGRESS_FIELDS = {
    "verified_claim_count",
    "verified_critical_claim_count",
    "accepted_certificate_count",
    "closed_obligation_count",
    "localized_conflict_count",
    "open_conflict_scope_size",
    "novel_hypothesis_count",
}
_CONTROL_FIELDS = {
    "iteration",
    "transition_count",
    "stagnant_rounds",
    "strategy_index",
    "terminal_status",
}
DEFAULT_LOOP_POLICY = {
    "stagnation_before_strategy_change": 2,
    "strategy_count": 8,
    "max_transitions": 64,
}
HYPOTHESIS_OPERATORS = (
    "edge-case",
    "counterexample",
    "inverse-reasoning",
    "event-reordering",
    "reference-frame-transform",
    "invariant",
    "symmetry",
    "analogy-transfer",
    "hidden-degree-of-freedom",
    "alternative-decomposition",
)
HYPOTHESIS_STATUSES = (
    "candidate",
    "queued",
    "testing",
    "rejected",
    "promoted",
    "superseded",
)
_HYPOTHESIS_FIELDS = {
    "id",
    "snapshot_version",
    "challenge_id",
    "operator",
    "proposal",
    "explains_gap",
    "novelty_basis",
    "falsification",
    "affected_claim_ids",
    "status",
}
_FALSIFICATION_FIELDS = {
    "test_type",
    "procedure",
    "expected_observation",
    "failure_observation",
}
_OPERATOR_PROBES = {
    "edge-case": "Test limiting values, boundary ownership, zero, infinity, and transition instants.",
    "counterexample": "Construct one admissible state that would falsify the current universal Claim.",
    "inverse-reasoning": "Assume the candidate conclusion, solve constraints backward, and check the source state.",
    "event-reordering": "Enumerate admissible events and test a different chronological ordering.",
    "reference-frame-transform": "Re-express the local conflict in another declared coordinate or reference frame.",
    "invariant": "Search for a conserved or monotone quantity that crosses the disputed stages.",
    "symmetry": "Test whether an assumed symmetry holds and what changes when it is broken.",
    "analogy-transfer": "Transfer a structurally similar reviewed model, then audit every changed condition.",
    "hidden-degree-of-freedom": "Search for an omitted branch, sign, direction, phase, or independent state variable.",
    "alternative-decomposition": "Split the process at a different physical event and rebuild only the affected interfaces.",  # noqa: E501
}
_CONFLICT_OPERATOR_PREFERENCES = {
    "order": {"event-reordering", "edge-case", "counterexample", "hidden-degree-of-freedom"},
    "interface": {"reference-frame-transform", "alternative-decomposition", "invariant"},
    "component": {"counterexample", "inverse-reasoning", "invariant", "analogy-transfer"},
    "omitted-condition": {"edge-case", "hidden-degree-of-freedom", "counterexample"},
    "decomposition": {"alternative-decomposition", "hidden-degree-of-freedom", "analogy-transfer"},
}


def _text(value: Any, field: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    normalized = structured_text.reject_unsupported_controls(value, field).strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def _identifier(value: Any, field: str) -> str:
    normalized = _text(value, field, 80)
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be a stable ASCII identifier")
    return normalized


def _string_map(value: Any, field: str) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError(f"{field} must be an object with at most 32 items")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _identifier(raw_key, f"{field} key")
        result[key] = _text(raw_value, f"{field}.{key}", 240)
    return result


_SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _state_key(value: Any, field: str) -> str:
    raw = _text(value, field, 80).translate(_SUBSCRIPT_TRANSLATION)
    parts: list[str] = []
    for char in raw:
        if char.isascii() and (char.isalnum() or char in {"_", ".", "-"}):
            parts.append(char)
        elif char.isspace():
            parts.append("_")
        else:
            name = unicodedata.name(char, "")
            if name.startswith("GREEK "):
                parts.append("u" + format(ord(char), "04x"))
            else:
                parts.append("_")
    normalized = re.sub(r"_+", "_", "".join(parts)).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = "state_" + normalized
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} cannot be normalized to a stable state key")
    return normalized


def _state_key_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError(f"{field} must be an array with at most 64 items")
    result = [_state_key(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicate normalized keys")
    return result


def _direction_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("directions must be an object with at most 32 items")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _text(raw_key, "directions key", 32)
        if not _DIRECTION_KEY_PATTERN.fullmatch(key):
            raise ValueError("directions key must be a stable signed ASCII axis")
        result[key] = _text(raw_value, f"directions.{key}", 240)
    return result


def _json_state(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError(f"{field} must be an object with at most 64 items")
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _state_key(raw_key, f"{field} key")
        try:
            serialized = json.dumps(
                raw_value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}.{key} must be finite JSON") from exc
        if len(serialized.encode("utf-8")) > 4_096:
            raise ValueError(f"{field}.{key} exceeds 4096 bytes")
        result[key] = json.loads(serialized)
    return result


def _id_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError(f"{field} must be an array with at most 64 items")
    result = [_identifier(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    return result


def _optional_transform(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field, 240)


def normalize_stage_interface(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _INTERFACE_FIELDS:
        raise ValueError("stage interface fields are invalid")
    entry_state = _json_state(payload["entry_state"], "entry_state")
    exit_state = _json_state(payload["exit_state"], "exit_state")
    required = _state_key_list(payload["required_entry_keys"], "required_entry_keys")
    carried = _state_key_list(payload["carried_state_keys"], "carried_state_keys")
    if not set(required).issubset(entry_state):
        raise ValueError("required_entry_keys must exist in entry_state")
    return {
        "stage_id": _identifier(payload["stage_id"], "stage_id"),
        "coordinate_frame": _text(payload["coordinate_frame"], "coordinate_frame", 240),
        "time_origin": _text(payload["time_origin"], "time_origin", 240),
        "directions": _direction_map(payload["directions"]),
        "entry_state": entry_state,
        "exit_state": exit_state,
        "required_entry_keys": required,
        "carried_state_keys": carried,
    }


def normalize_stage_transition(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _TRANSITION_FIELDS:
        raise ValueError("stage transition interface fields are invalid")
    raw_mapping = payload["state_mapping"]
    if not isinstance(raw_mapping, list) or len(raw_mapping) > 64:
        raise ValueError("state_mapping must be an array with at most 64 items")
    mapping = []
    target_keys: set[str] = set()
    for raw in raw_mapping:
        if not isinstance(raw, dict) or set(raw) != _MAPPING_FIELDS:
            raise ValueError("state_mapping item fields are invalid")
        item = {
            "from_key": _state_key(raw["from_key"], "state_mapping.from_key"),
            "to_key": _state_key(raw["to_key"], "state_mapping.to_key"),
            "transform": _optional_transform(raw["transform"], "state_mapping.transform"),
        }
        if item["to_key"] in target_keys:
            raise ValueError("state_mapping cannot assign an entry key twice")
        target_keys.add(item["to_key"])
        mapping.append(item)
    introduced = _state_key_list(payload["introduced_entry_keys"], "introduced_entry_keys")
    if set(introduced) & target_keys:
        raise ValueError("introduced_entry_keys cannot also be assigned by state_mapping")
    return {
        "from_stage": _identifier(payload["from_stage"], "from_stage"),
        "to_stage": _identifier(payload["to_stage"], "to_stage"),
        "event": _text(payload["event"], "event", 300),
        "state_mapping": mapping,
        "introduced_entry_keys": introduced,
        "coordinate_transform": _optional_transform(payload["coordinate_transform"], "coordinate_transform"),
        "time_transform": _optional_transform(payload["time_transform"], "time_transform"),
        "direction_transform": _optional_transform(payload["direction_transform"], "direction_transform"),
    }


def _issue(
    code: str,
    *,
    from_stage: str,
    to_stage: str,
    message: str,
    semantic_required: bool = False,
    state_key: str | None = None,
) -> dict[str, Any]:
    result = {
        "code": code,
        "from_stage": from_stage,
        "to_stage": to_stage,
        "message": message,
        "semantic_required": semantic_required,
    }
    if state_key is not None:
        result["state_key"] = state_key
    return result


def check_stage_interfaces(
    interfaces: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check assembly compatibility without interpreting free-text transforms."""
    normalized_interfaces = [normalize_stage_interface(item) for item in interfaces]
    by_stage = {item["stage_id"]: item for item in normalized_interfaces}
    if len(by_stage) != len(normalized_interfaces):
        raise ValueError("stage interfaces must have unique stage_id values")
    normalized_transitions = [normalize_stage_transition(item) for item in transitions]
    issues: list[dict[str, Any]] = []

    def has_temporal_state(*stages: dict[str, Any]) -> bool:
        return any(
            re.search(r"(?:^|_)(?:t|time|duration|velocity|speed|position)(?:_|$)", key)
            for stage in stages
            for state_name in ("entry_state", "exit_state")
            for key in stage[state_name]
        )

    for transition in normalized_transitions:
        from_id = transition["from_stage"]
        to_id = transition["to_stage"]
        if from_id not in by_stage or to_id not in by_stage:
            raise ValueError("transition references an unknown stage interface")
        source = by_stage[from_id]
        target = by_stage[to_id]
        for field, transform_field, code in (
            ("coordinate_frame", "coordinate_transform", "coordinate-frame-mismatch"),
            ("time_origin", "time_transform", "time-origin-mismatch"),
            ("directions", "direction_transform", "direction-mismatch"),
        ):
            if source[field] == target[field]:
                continue
            if field == "time_origin" and not has_temporal_state(source, target):
                continue
            transform = transition[transform_field]
            if transform is None:
                issues.append(
                    _issue(
                        code,
                        from_stage=from_id,
                        to_stage=to_id,
                        message=f"{field} differs without an explicit transform",
                    )
                )
            else:
                issues.append(
                    _issue(
                        f"{code}-transform-unverified",
                        from_stage=from_id,
                        to_stage=to_id,
                        message=f"{field} differs; transform requires semantic verification",
                        semantic_required=True,
                    )
                )

        mapped_targets = {item["to_key"] for item in transition["state_mapping"]}
        for required_key in target["required_entry_keys"]:
            if required_key not in mapped_targets and required_key not in transition["introduced_entry_keys"]:
                issues.append(
                    _issue(
                        "missing-entry-state",
                        from_stage=from_id,
                        to_stage=to_id,
                        state_key=required_key,
                        message=f"required entry state {required_key} is not mapped",
                    )
                )
        for introduced_key in transition["introduced_entry_keys"]:
            if introduced_key not in target["entry_state"]:
                issues.append(
                    _issue(
                        "unknown-introduced-entry-state",
                        from_stage=from_id,
                        to_stage=to_id,
                        state_key=introduced_key,
                        message=(f"introduced entry state {introduced_key} does not exist in target entry_state"),
                    )
                )
        for mapping in transition["state_mapping"]:
            source_key = mapping["from_key"]
            target_key = mapping["to_key"]
            if source_key not in source["exit_state"]:
                issues.append(
                    _issue(
                        "unknown-exit-state",
                        from_stage=from_id,
                        to_stage=to_id,
                        state_key=source_key,
                        message=f"mapped exit state {source_key} does not exist",
                    )
                )
                continue
            if target_key not in target["entry_state"]:
                issues.append(
                    _issue(
                        "unknown-entry-state",
                        from_stage=from_id,
                        to_stage=to_id,
                        state_key=target_key,
                        message=f"mapped entry state {target_key} does not exist",
                    )
                )
                continue
            if source["exit_state"][source_key] == target["entry_state"][target_key]:
                continue
            if mapping["transform"] is None:
                issues.append(
                    _issue(
                        "state-value-mismatch",
                        from_stage=from_id,
                        to_stage=to_id,
                        state_key=target_key,
                        message=(f"{source_key} exit value does not match {target_key} entry value"),
                    )
                )
            else:
                issues.append(
                    _issue(
                        "state-transform-unverified",
                        from_stage=from_id,
                        to_stage=to_id,
                        state_key=target_key,
                        message="state transform requires semantic verification",
                        semantic_required=True,
                    )
                )

    hard_issues = [item for item in issues if not item["semantic_required"]]
    semantic_issues = [item for item in issues if item["semantic_required"]]
    if hard_issues:
        status = "conflict"
    elif semantic_issues:
        status = "provisional"
    else:
        status = "pass"
    logger.info(
        "stage=interface_check status=%s interface_count=%d transition_count=%d hard_issues=%d semantic_issues=%d",
        status,
        len(normalized_interfaces),
        len(normalized_transitions),
        len(hard_issues),
        len(semantic_issues),
    )
    return {
        "status": status,
        "interface_count": len(normalized_interfaces),
        "transition_count": len(normalized_transitions),
        "issues": issues,
        "hard_issue_count": len(hard_issues),
        "semantic_required_count": len(semantic_issues),
    }


def _specific_text(value: Any, field: str) -> str:
    normalized = _text(value, field, 500)
    if len(normalized) < 8 or normalized.lower() in _VAGUE_CHALLENGE_TEXT:
        raise ValueError(f"{field} must be specific and testable")
    return normalized


def normalize_challenge_ticket(
    payload: dict[str, Any],
    *,
    available_claim_ids: set[str] | None = None,
    agent_submission: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _CHALLENGE_FIELDS:
        raise ValueError("challenge ticket fields are invalid")
    snapshot_version = payload["snapshot_version"]
    if isinstance(snapshot_version, bool) or not isinstance(snapshot_version, int) or snapshot_version < 1:
        raise ValueError("challenge snapshot_version must be a positive integer")
    trigger = _text(payload["trigger"], "challenge.trigger", 40).lower()
    if trigger not in CHALLENGE_TRIGGERS:
        raise ValueError("challenge trigger is invalid")
    claim_ids = _id_list(payload["claim_ids"], "challenge.claim_ids")
    if not claim_ids:
        raise ValueError("challenge.claim_ids must not be empty")
    if available_claim_ids is not None:
        unknown = set(claim_ids) - available_claim_ids
        if unknown:
            raise ValueError(f"challenge references unknown claims: {sorted(unknown)}")
    backjump = _identifier(payload["suggested_backjump"], "challenge.suggested_backjump")
    if backjump not in claim_ids:
        raise ValueError("challenge suggested_backjump must be one of claim_ids")
    status = _text(payload["status"], "challenge.status", 32).lower()
    if status not in CHALLENGE_STATUSES:
        raise ValueError("challenge status is invalid")
    if agent_submission and status != "candidate":
        raise ValueError("Agent-submitted challenge status must be candidate")
    return {
        "id": _identifier(payload["id"], "challenge.id"),
        "snapshot_version": snapshot_version,
        "trigger": trigger,
        "claim_ids": claim_ids,
        "specific_doubt": _specific_text(payload["specific_doubt"], "challenge.specific_doubt"),
        "falsification_test": _specific_text(payload["falsification_test"], "challenge.falsification_test"),
        "suggested_backjump": backjump,
        "status": status,
    }


def challenge_from_interface_issue(
    issue: dict[str, Any],
    *,
    challenge_id: str,
    snapshot_version: int,
    claim_ids: list[str],
    suggested_backjump: str,
) -> dict[str, Any]:
    """Translate one located interface issue without mutating the proof graph."""
    code = str(issue.get("code", "")).strip()
    from_stage = str(issue.get("from_stage", "")).strip()
    to_stage = str(issue.get("to_stage", "")).strip()
    state_key = str(issue.get("state_key", "")).strip()
    if not code or not from_stage or not to_stage:
        raise ValueError("interface issue is missing location fields")
    focus = f" state {state_key}" if state_key else ""
    doubt = (
        f"Interface {from_stage}->{to_stage}{focus} reports {code}; "
        "the downstream stage may start from an incompatible state."
    )
    falsification = (
        f"Freeze {from_stage} exit and {to_stage} entry snapshots, then compare "
        f"the declared transform and mapped value for{focus or ' all required states'}."
    )
    return normalize_challenge_ticket({
        "id": challenge_id,
        "snapshot_version": snapshot_version,
        "trigger": "interface-mismatch",
        "claim_ids": claim_ids,
        "specific_doubt": doubt,
        "falsification_test": falsification,
        "suggested_backjump": suggested_backjump,
        "status": "candidate",
    })


def normalize_conflict_evidence(payload: dict[str, Any]) -> dict[str, list[str]]:
    if not isinstance(payload, dict) or set(payload) != _CONFLICT_EVIDENCE_FIELDS:
        raise ValueError("conflict evidence fields are invalid")
    result = {field: _id_list(payload[field], f"conflict_evidence.{field}") for field in _CONFLICT_EVIDENCE_FIELDS}
    return result


def diagnose_minimal_conflict(
    challenge: dict[str, Any],
    claims: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Classify a conflict and reduce its causal frontier to root-most Claims."""
    active = claim_ledger.active_claims(claims)
    ticket = normalize_challenge_ticket(
        challenge,
        available_claim_ids=set(active),
        agent_submission=False,
    )
    normalized_evidence = normalize_conflict_evidence(evidence)
    evidence_claim_ids = (
        set(normalized_evidence["event_order_claim_ids"])
        | set(normalized_evidence["conflicting_claim_ids"])
        | set(normalized_evidence["component_failure_claim_ids"])
    )
    unknown = evidence_claim_ids - set(active)
    if unknown:
        raise ValueError(f"conflict evidence references unknown claims: {sorted(unknown)}")
    unbound = evidence_claim_ids - set(ticket["claim_ids"])
    if unbound:
        raise ValueError(f"conflict evidence is outside the challenge binding: {sorted(unbound)}")
    candidates = evidence_claim_ids or {ticket["suggested_backjump"]}
    downstream_by_candidate = {
        claim_id: set(claim_ledger.dependency_impact_cone(claims, {claim_id})) for claim_id in candidates
    }
    minimal_roots = sorted(
        claim_id
        for claim_id in candidates
        if not any(claim_id in downstream_by_candidate[other_id] for other_id in candidates if other_id != claim_id)
    )

    if normalized_evidence["interface_issue_codes"]:
        conflict_class = "interface"
        rationale = "stage interface checker located an incompatibility"
    elif normalized_evidence["event_order_claim_ids"]:
        conflict_class = "order"
        rationale = "event-order checker located an ordering or root-selection conflict"
    elif normalized_evidence["missing_obligation_ids"]:
        conflict_class = "omitted-condition"
        rationale = "a declared verification obligation is not covered"
    elif normalized_evidence["missing_target_ids"]:
        conflict_class = "decomposition"
        rationale = "the proof graph does not cover a question target"
    elif normalized_evidence["component_failure_claim_ids"] or normalized_evidence["conflicting_claim_ids"]:
        conflict_class = "component"
        rationale = "one or more component Claims fail their accepted verification route"
    else:
        conflict_class = "decomposition"
        rationale = "no narrower deterministic cause is available; revisit decomposition"

    logger.info(
        "stage=conflict_diagnosis challenge_id=%s conflict_class=%s minimal_root_count=%d",
        ticket["id"],
        conflict_class,
        len(minimal_roots),
    )
    return {
        "challenge_id": ticket["id"],
        "snapshot_version": ticket["snapshot_version"],
        "conflict_class": conflict_class,
        "minimal_conflict_claim_ids": minimal_roots,
        "suggested_backjump": (minimal_roots[0] if len(minimal_roots) == 1 else ticket["suggested_backjump"]),
        "rationale": rationale,
        "evidence": normalized_evidence,
    }


def invalidate_dependency_cone(
    claims: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
    *,
    root_claim_ids: set[str],
    snapshot_version: int,
    challenge_id: str,
) -> dict[str, Any]:
    """Invalidate only a causal subgraph and plan versioned rebuilding."""
    if isinstance(snapshot_version, bool) or not isinstance(snapshot_version, int) or snapshot_version < 1:
        raise ValueError("snapshot_version must be a positive integer")
    normalized_challenge_id = _identifier(challenge_id, "challenge_id")
    active = claim_ledger.active_claims(claims)
    if not root_claim_ids:
        raise ValueError("root_claim_ids must not be empty")
    unknown = root_claim_ids - set(active)
    if unknown:
        raise ValueError(f"backjump roots are unknown: {sorted(unknown)}")
    affected = set(root_claim_ids)
    affected.update(claim_ledger.dependency_impact_cone(claims, root_claim_ids, include_changed=False))
    logger.info(
        "stage=backjump challenge_id=%s root_count=%d affected_count=%d",
        normalized_challenge_id,
        len(root_claim_ids),
        len(affected),
    )
    topological_order = claim_ledger.topological_claim_ids(claims)
    rebuild_order = [claim_id for claim_id in topological_order if claim_id in affected]

    invalidated_claims: list[dict[str, Any]] = []
    preserved_claims: list[dict[str, Any]] = []
    for claim_id in topological_order:
        claim = active[claim_id]
        if claim_id not in affected:
            preserved_claims.append(claim)
            continue
        if claim["status"] in {"candidate", "verified"}:
            status = correctness_policy.require_claim_status_transition(claim["status"], "disputed")
        elif claim["status"] == "disputed":
            status = "disputed"
        else:
            raise ValueError(f"claim {claim_id} status cannot be invalidated in place")
        invalidated_claims.append({**claim, "status": status})

    normalized_certificates = [claim_ledger.normalize_certificate(item) for item in certificates]
    preserved_certificates = [item for item in normalized_certificates if item["claim_id"] not in affected]
    invalidated_certificates = [item for item in normalized_certificates if item["claim_id"] in affected]
    rebuild_tasks = []
    for claim_id in rebuild_order:
        claim = active[claim_id]
        next_version = claim_ledger.next_claim_version(claim_id, claims)
        action = "rebuild-root" if claim_id in root_claim_ids else "rebuild-downstream"
        rebuild_tasks.append({
            "claim_id": claim_id,
            "from_version": claim["version"],
            "next_version": next_version,
            "action": action,
            "depends_on_rebuild_ids": [
                dependency_id for dependency_id in claim["depends_on"] if dependency_id in affected
            ],
        })

    return {
        "challenge_id": normalized_challenge_id,
        "from_snapshot_version": snapshot_version,
        "next_snapshot_version": snapshot_version + 1,
        "root_claim_ids": sorted(root_claim_ids),
        "affected_claim_ids": rebuild_order,
        "preserved_claim_ids": [claim_id for claim_id in topological_order if claim_id not in affected],
        "invalidated_claims": invalidated_claims,
        "preserved_claims": preserved_claims,
        "invalidated_certificates": invalidated_certificates,
        "preserved_certificates": preserved_certificates,
        "rebuild_tasks": rebuild_tasks,
    }


def deduplicate_atomic_task(
    task: dict[str, Any],
    existing_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Guarantee that an identical task fingerprint never calls a provider twice."""
    normalized = claim_ledger.normalize_atomic_task(task)
    fingerprint = claim_ledger.atomic_task_fingerprint(normalized)
    for raw in existing_tasks:
        existing = claim_ledger.normalize_atomic_task(raw)
        if claim_ledger.atomic_task_fingerprint(existing) != fingerprint:
            continue
        decision = {
            "completed": "reuse",
            "pending": "wait",
            "running": "wait",
            "failed": "reject-duplicate",
            "superseded": "reject-duplicate",
        }[existing["status"]]
        return {
            "decision": decision,
            "provider_call_allowed": False,
            "fingerprint": fingerprint,
            "matched_task_id": existing["task_id"],
            "matched_status": existing["status"],
        }
    return {
        "decision": "execute",
        "provider_call_allowed": True,
        "fingerprint": fingerprint,
        "matched_task_id": None,
        "matched_status": None,
    }


def normalize_progress_state(payload: dict[str, Any]) -> dict[str, int]:
    if not isinstance(payload, dict) or set(payload) != _PROGRESS_FIELDS:
        raise ValueError("progress state fields are invalid")
    result: dict[str, int] = {}
    for field in _PROGRESS_FIELDS:
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"progress state {field} must be a non-negative integer")
        result[field] = value
    return result


def evaluate_progress(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Recognize evidence gain, not token spend or repeated model activity."""
    before = normalize_progress_state(previous)
    after = normalize_progress_state(current)
    improvements: list[str] = []
    for field in (
        "verified_critical_claim_count",
        "accepted_certificate_count",
        "closed_obligation_count",
        "localized_conflict_count",
        "novel_hypothesis_count",
        "verified_claim_count",
    ):
        if after[field] > before[field]:
            improvements.append(field)
    if (
        before["open_conflict_scope_size"] > 0
        and after["open_conflict_scope_size"] < before["open_conflict_scope_size"]
    ):
        improvements.append("open_conflict_scope_reduced")
    return {
        "progressed": bool(improvements),
        "improvements": improvements,
        "previous": before,
        "current": after,
    }


def normalize_loop_control(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _CONTROL_FIELDS:
        raise ValueError("loop control fields are invalid")
    result: dict[str, Any] = {}
    for field in (
        "iteration",
        "transition_count",
        "stagnant_rounds",
        "strategy_index",
    ):
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"loop control {field} must be a non-negative integer")
        result[field] = value
    terminal_status = str(payload["terminal_status"]).strip().upper()
    if terminal_status not in {"ACTIVE", "VERIFIED", "PROVISIONAL", "UNRESOLVED"}:
        raise ValueError("loop control terminal_status is invalid")
    result["terminal_status"] = terminal_status
    return result


def initial_loop_control() -> dict[str, Any]:
    return {
        "iteration": 0,
        "transition_count": 0,
        "stagnant_rounds": 0,
        "strategy_index": 0,
        "terminal_status": "ACTIVE",
    }


def advance_loop_control(
    control: dict[str, Any],
    progress: dict[str, Any],
    *,
    evidence_status: str,
    has_open_conflict: bool,
    policy: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Advance, switch strategy, or fail-safe stop; never infer VERIFIED."""
    current = normalize_loop_control(control)
    if current["terminal_status"] != "ACTIVE":
        raise ValueError("terminal loop control cannot be advanced")
    if not isinstance(progress, dict) or "progressed" not in progress:
        raise ValueError("progress assessment is invalid")
    normalized_evidence = str(evidence_status).strip().upper()
    if normalized_evidence not in {"VERIFIED", "PROVISIONAL", "UNRESOLVED"}:
        raise ValueError("evidence_status is invalid")
    logger.info(
        "stage=loop_advance iteration=%d transition_count=%d stagnant_rounds=%d strategy_index=%d evidence_status=%s",
        current["iteration"],
        current["transition_count"],
        current["stagnant_rounds"],
        current["strategy_index"],
        normalized_evidence,
    )
    settings = dict(DEFAULT_LOOP_POLICY)
    if policy is not None:
        if set(policy) != set(DEFAULT_LOOP_POLICY):
            raise ValueError("loop policy fields are invalid")
        settings.update(policy)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in settings.values()):
        raise ValueError("loop policy values must be positive integers")

    next_control = {
        **current,
        "iteration": current["iteration"] + 1,
        "transition_count": current["transition_count"] + 1,
    }
    if normalized_evidence == "VERIFIED":
        next_control["terminal_status"] = "VERIFIED"
        logger.info("stage=loop_advance action=stop-verified iteration=%d", next_control["iteration"])
        return {
            "action": "stop-verified",
            "control": next_control,
            "provider_call_allowed": False,
            "reason": "proof aggregator reports complete current certificate coverage",
        }

    if next_control["transition_count"] >= settings["max_transitions"]:
        next_control["terminal_status"] = "UNRESOLVED" if has_open_conflict else "PROVISIONAL"
        logger.warning(
            "stage=loop_advance action=hard-fuse transition_count=%d max=%d terminal_status=%s",
            next_control["transition_count"],
            settings["max_transitions"],
            next_control["terminal_status"],
        )
        return {
            "action": "hard-fuse",
            "control": next_control,
            "provider_call_allowed": False,
            "reason": "safety transition cap reached without verified evidence",
        }

    if bool(progress["progressed"]):
        next_control["stagnant_rounds"] = 0
        return {
            "action": "continue",
            "control": next_control,
            "provider_call_allowed": True,
            "reason": "measurable evidence or conflict-localization progress",
        }

    next_control["stagnant_rounds"] = current["stagnant_rounds"] + 1
    if next_control["stagnant_rounds"] >= settings["stagnation_before_strategy_change"]:
        if next_control["strategy_index"] + 1 < settings["strategy_count"]:
            next_control["strategy_index"] += 1
            next_control["stagnant_rounds"] = 0
            logger.info(
                "stage=loop_advance action=switch-strategy new_strategy_index=%d stagnant_rounds_reset",
                next_control["strategy_index"],
            )
            return {
                "action": "switch-strategy",
                "control": next_control,
                "provider_call_allowed": True,
                "reason": "stagnation threshold reached; identical retry is forbidden",
            }
        next_control["terminal_status"] = "UNRESOLVED" if has_open_conflict else "PROVISIONAL"
        logger.warning(
            "stage=loop_advance action=strategy-fuse all_strategies_exhausted terminal_status=%s",
            next_control["terminal_status"],
        )
        return {
            "action": "strategy-fuse",
            "control": next_control,
            "provider_call_allowed": False,
            "reason": "all distinct strategies exhausted without verified evidence",
        }
    return {
        "action": "continue-no-retry",
        "control": next_control,
        "provider_call_allowed": False,
        "reason": "no progress; wait for a strategy change instead of repeating",
    }


def normalize_hypothesis(
    payload: dict[str, Any],
    *,
    available_claim_ids: set[str] | None = None,
    agent_submission: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != _HYPOTHESIS_FIELDS:
        raise ValueError("hypothesis fields are invalid")
    snapshot_version = payload["snapshot_version"]
    if isinstance(snapshot_version, bool) or not isinstance(snapshot_version, int) or snapshot_version < 1:
        raise ValueError("hypothesis snapshot_version must be a positive integer")
    operator_name = _text(payload["operator"], "hypothesis.operator", 64).lower()
    if operator_name not in HYPOTHESIS_OPERATORS:
        raise ValueError("hypothesis operator is invalid")
    affected = _id_list(payload["affected_claim_ids"], "hypothesis.affected_claim_ids")
    if not affected:
        raise ValueError("hypothesis.affected_claim_ids must not be empty")
    if available_claim_ids is not None:
        unknown = set(affected) - available_claim_ids
        if unknown:
            raise ValueError(f"hypothesis affects unknown claims: {sorted(unknown)}")
    falsification = payload["falsification"]
    if not isinstance(falsification, dict) or set(falsification) != _FALSIFICATION_FIELDS:
        raise ValueError("hypothesis falsification fields are invalid")
    test_type = _text(falsification["test_type"], "hypothesis.falsification.test_type", 32).lower()
    if test_type not in {"deterministic", "independent-agent", "teacher"}:
        raise ValueError("hypothesis falsification test_type is invalid")
    status = _text(payload["status"], "hypothesis.status", 32).lower()
    if status not in HYPOTHESIS_STATUSES:
        raise ValueError("hypothesis status is invalid")
    if agent_submission and status != "candidate":
        raise ValueError("Agent-submitted hypothesis status must be candidate")
    return {
        "id": _identifier(payload["id"], "hypothesis.id"),
        "snapshot_version": snapshot_version,
        "challenge_id": _identifier(payload["challenge_id"], "hypothesis.challenge_id"),
        "operator": operator_name,
        "proposal": _specific_text(payload["proposal"], "hypothesis.proposal"),
        "explains_gap": _specific_text(payload["explains_gap"], "hypothesis.explains_gap"),
        "novelty_basis": _specific_text(payload["novelty_basis"], "hypothesis.novelty_basis"),
        "falsification": {
            "test_type": test_type,
            "procedure": _specific_text(
                falsification["procedure"],
                "hypothesis.falsification.procedure",
            ),
            "expected_observation": _specific_text(
                falsification["expected_observation"],
                "hypothesis.falsification.expected_observation",
            ),
            "failure_observation": _specific_text(
                falsification["failure_observation"],
                "hypothesis.falsification.failure_observation",
            ),
        },
        "affected_claim_ids": affected,
        "status": status,
    }


def hypothesis_fingerprint(hypothesis: dict[str, Any]) -> str:
    normalized = normalize_hypothesis(hypothesis, agent_submission=False)
    return claim_ledger.stable_fingerprint(
        "hypothesis-v1",
        {
            key: normalized[key]
            for key in (
                "snapshot_version",
                "challenge_id",
                "operator",
                "proposal",
                "explains_gap",
                "novelty_basis",
                "falsification",
                "affected_claim_ids",
            )
        },
    )


def operator_probes(
    challenge: dict[str, Any],
    claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    active = claim_ledger.active_claims(claims)
    ticket = normalize_challenge_ticket(
        challenge,
        available_claim_ids=set(active),
        agent_submission=False,
    )
    return [
        {
            "operator": operator_name,
            "challenge_id": ticket["id"],
            "snapshot_version": ticket["snapshot_version"],
            "affected_claim_ids": list(ticket["claim_ids"]),
            "probe": _OPERATOR_PROBES[operator_name],
            "required_output": [
                "proposal",
                "explains_gap",
                "novelty_basis",
                "falsification.test_type",
                "falsification.procedure",
                "falsification.expected_observation",
                "falsification.failure_observation",
            ],
        }
        for operator_name in HYPOTHESIS_OPERATORS
    ]


def add_hypothesis_to_pool(
    hypothesis: dict[str, Any],
    challenge: dict[str, Any],
    claims: list[dict[str, Any]],
    existing_hypotheses: list[dict[str, Any]],
    *,
    maximum_pool_size: int = 32,
) -> dict[str, Any]:
    """Gate relevance, novelty, and falsifiability before isolated admission."""
    if maximum_pool_size < 1:
        raise ValueError("maximum_pool_size must be positive")
    active = claim_ledger.active_claims(claims)
    ticket = normalize_challenge_ticket(
        challenge,
        available_claim_ids=set(active),
        agent_submission=False,
    )
    candidate = normalize_hypothesis(hypothesis, available_claim_ids=set(active))
    pool = [
        normalize_hypothesis(item, available_claim_ids=set(active), agent_submission=False)
        for item in existing_hypotheses
    ]
    reasons: list[str] = []
    if candidate["challenge_id"] != ticket["id"]:
        reasons.append("hypothesis is bound to a different challenge")
    if candidate["snapshot_version"] != ticket["snapshot_version"]:
        reasons.append("hypothesis is bound to a stale snapshot")
    if not set(candidate["affected_claim_ids"]).issubset(ticket["claim_ids"]):
        reasons.append("hypothesis affects Claims outside the challenge")
    candidate_fingerprint = hypothesis_fingerprint(candidate)
    if any(hypothesis_fingerprint(item) == candidate_fingerprint for item in pool):
        reasons.append("hypothesis duplicates an existing candidate")
    proposal_key = " ".join(candidate["proposal"].lower().split())
    existing_claim_statements = {" ".join(item["statement"].lower().split()) for item in active.values()}
    if proposal_key in existing_claim_statements:
        reasons.append("hypothesis merely repeats an existing Claim")
    if len(pool) >= maximum_pool_size:
        reasons.append("hypothesis pool is full; unresolved status must be preserved")
    if reasons:
        logger.info(
            "stage=hypothesis_admission decision=rejected challenge_id=%s reason_count=%d",
            ticket["id"],
            len(reasons),
        )
        return {
            "decision": "rejected",
            "reasons": reasons,
            "fingerprint": candidate_fingerprint,
            "pool": pool,
        }
    logger.info(
        "stage=hypothesis_admission decision=accepted challenge_id=%s hypothesis_id=%s pool_size=%d",
        ticket["id"],
        candidate["id"],
        len(pool) + 1,
    )
    return {
        "decision": "accepted",
        "reasons": [],
        "fingerprint": candidate_fingerprint,
        "pool": [*pool, candidate],
    }


def _operator_history(
    operator_name: str,
    operator_stats: dict[str, dict[str, int]],
) -> dict[str, int]:
    raw = operator_stats.get(operator_name, {})
    required = {
        "attempt_count",
        "conflict_found_count",
        "certificate_gain_count",
    }
    if set(raw) - required:
        raise ValueError(f"operator stats for {operator_name} has unknown fields")
    result = {field: raw.get(field, 0) for field in required}
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in result.values()):
        raise ValueError("operator stats values must be non-negative integers")
    if (
        result["conflict_found_count"] > result["attempt_count"]
        or result["certificate_gain_count"] > result["attempt_count"]
    ):
        raise ValueError("operator success counts cannot exceed attempts")
    return result


def score_hypothesis(
    hypothesis: dict[str, Any],
    *,
    conflict_class: str,
    claim_risks: dict[str, float],
    operator_stats: dict[str, dict[str, int]],
) -> dict[str, Any]:
    normalized = normalize_hypothesis(hypothesis, agent_submission=False)
    conflict = str(conflict_class).strip().lower()
    if conflict not in CONFLICT_CLASSES:
        raise ValueError("conflict_class is invalid")
    risks = []
    for claim_id in normalized["affected_claim_ids"]:
        value = claim_risks.get(claim_id, 0.5)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("claim risk must be numeric")
        numeric = float(value)
        if not 0.0 <= numeric <= 1.0:
            raise ValueError("claim risk must be between 0 and 1")
        risks.append(numeric)
    risk_score = max(risks, default=0.5)
    history = _operator_history(normalized["operator"], operator_stats)
    successes = history["conflict_found_count"] + history["certificate_gain_count"]
    discovery_yield = (successes + 1.0) / (2.0 * history["attempt_count"] + 2.0)
    compatibility = 1.0 if normalized["operator"] in _CONFLICT_OPERATOR_PREFERENCES[conflict] else 0.45
    testability = {
        "deterministic": 1.0,
        "independent-agent": 0.75,
        "teacher": 0.55,
    }[normalized["falsification"]["test_type"]]
    diversity = 1.0 / (1.0 + 0.08 * history["attempt_count"])
    score = 0.35 * risk_score + 0.25 * discovery_yield + 0.20 * compatibility + 0.15 * testability + 0.05 * diversity
    return {
        "hypothesis_id": normalized["id"],
        "operator": normalized["operator"],
        "score": round(max(score, 0.000001), 8),
        "components": {
            "risk": round(risk_score, 8),
            "historical_discovery_yield": round(discovery_yield, 8),
            "conflict_compatibility": compatibility,
            "testability": testability,
            "diversity": round(diversity, 8),
        },
    }


def select_hypothesis(
    hypotheses: list[dict[str, Any]],
    *,
    conflict_class: str,
    claim_risks: dict[str, float],
    operator_stats: dict[str, dict[str, int]],
    random_seed: int,
    exploration_rate: float = 0.1,
) -> dict[str, Any]:
    """Select a search candidate reproducibly; selection never promotes truth."""
    if isinstance(random_seed, bool) or not isinstance(random_seed, int):
        raise ValueError("random_seed must be an integer")
    if (
        isinstance(exploration_rate, bool)
        or not isinstance(exploration_rate, (int, float))
        or not 0.0 <= float(exploration_rate) <= 1.0
    ):
        raise ValueError("exploration_rate must be between 0 and 1")
    candidates = [
        normalize_hypothesis(item, agent_submission=False)
        for item in hypotheses
        if str(item.get("status", "")).strip().lower() == "candidate"
    ]
    if not candidates:
        raise ValueError("no candidate hypothesis is available")
    candidates.sort(key=lambda item: (item["id"], hypothesis_fingerprint(item)))
    scores = [
        score_hypothesis(
            item,
            conflict_class=conflict_class,
            claim_risks=claim_risks,
            operator_stats=operator_stats,
        )
        for item in candidates
    ]
    rng = random.Random(random_seed)
    if rng.random() < float(exploration_rate):
        mode = "uniform-exploration"
        selected_index = rng.randrange(len(candidates))
    else:
        mode = "risk-weighted"
        selected_index = rng.choices(
            range(len(candidates)),
            weights=[item["score"] for item in scores],
            k=1,
        )[0]
    selected = candidates[selected_index]
    selection_fingerprint = claim_ledger.stable_fingerprint(
        "hypothesis-selection-v1",
        {
            "random_seed": random_seed,
            "exploration_rate": float(exploration_rate),
            "conflict_class": conflict_class,
            "candidate_fingerprints": [hypothesis_fingerprint(item) for item in candidates],
            "scores": scores,
            "mode": mode,
            "selected_id": selected["id"],
        },
    )
    return {
        "selected_hypothesis_id": selected["id"],
        "selected_operator": selected["operator"],
        "mode": mode,
        "random_seed": random_seed,
        "exploration_rate": float(exploration_rate),
        "scores": scores,
        "selection_fingerprint": selection_fingerprint,
        "truth_status": "candidate",
    }
