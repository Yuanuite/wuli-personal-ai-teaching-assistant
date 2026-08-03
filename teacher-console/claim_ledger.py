#!/usr/bin/env python3
"""Deterministic contracts for a versioned, claim-level proof ledger.

The ledger stores concise claims and verification artifacts, not hidden model
reasoning.  This module never calls a provider and never writes canonical files.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, cast

import correctness_policy
import structured_text
from log import get_logger

logger = get_logger("claim_ledger")

LEDGER_CONTRACT = "wuli.claim-ledger.v1"
CERTIFICATE_CONTRACT = "wuli.verification-certificate.v1"
ATOMIC_TASK_CONTRACT = "wuli.atomic-task.v1"
GRAPH_SNAPSHOT_CONTRACT = "wuli.claim-graph-snapshot.v1"

_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,79}$")
_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_CLAIM_FIELDS = {
    "id",
    "version",
    "kind",
    "statement",
    "target_ids",
    "stage_ids",
    "depends_on",
    "conditions",
    "obligation_ids",
    "check_spec",
    "status",
    "source",
}
_SOURCE_FIELDS = {"task_id", "input_fingerprint", "policy_version"}
_CERTIFICATE_FIELDS = {
    "claim_id",
    "claim_version",
    "verifier_kind",
    "check_type",
    "verdict",
    "normalized_result",
    "decisive_checks",
    "input_fingerprint",
    "verifier_identity",
}
_VERIFIER_IDENTITY_FIELDS = {
    "model_id",
    "provider",
    "context_isolated",
}
_TASK_FIELDS = {
    "task_id",
    "action",
    "target_ids",
    "input_snapshot",
    "input_fingerprint",
    "output_contract",
    "strategy",
    "random_seed",
    "status",
}
_SNAPSHOT_FIELDS = {
    "schema_version",
    "snapshot_version",
    "policy_version",
    "input_fingerprint",
    "claims",
    "certificates",
    "tasks",
}


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _require_exact_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing:
        raise ValueError(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"{label} has unknown fields: {', '.join(unknown)}")


def _clean_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    normalized = structured_text.reject_unsupported_controls(value, field).strip()
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def _clean_id(value: Any, field: str) -> str:
    normalized = _clean_text(value, field, 80)
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be a stable ASCII identifier")
    return normalized


def _clean_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _clean_fingerprint(value: Any, field: str) -> str:
    normalized = _clean_text(value, field, 64).lower()
    if not _FINGERPRINT_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be a 64-character lowercase SHA-256")
    return normalized


def _clean_id_list(
    value: Any,
    field: str,
    *,
    allow_empty: bool = True,
    maximum: int = 32,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for index, raw in enumerate(value):
        item = _clean_id(raw, f"{field}[{index}]")
        if item in result:
            raise ValueError(f"{field} must not contain duplicates")
        result.append(item)
    if not allow_empty and not result:
        raise ValueError(f"{field} must not be empty")
    if len(result) > maximum:
        raise ValueError(f"{field} must contain at most {maximum} items")
    return result


def _clean_text_list(
    value: Any,
    field: str,
    *,
    maximum_items: int = 24,
    maximum_text: int = 300,
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    result: list[str] = []
    for index, raw in enumerate(value):
        item = _clean_text(raw, f"{field}[{index}]", maximum_text)
        if item in result:
            raise ValueError(f"{field} must not contain duplicates")
        result.append(item)
    if len(result) > maximum_items:
        raise ValueError(f"{field} must contain at most {maximum_items} items")
    return result


def _clean_json_object(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    candidate = _require_object(value, field)
    try:
        serialized = json.dumps(
            candidate,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain finite JSON values") from exc
    if len(serialized.encode("utf-8")) > 16_384:
        raise ValueError(f"{field} exceeds 16384 bytes")
    return cast(dict[str, Any], json.loads(serialized))


def normalize_claim(
    payload: dict[str, Any],
    *,
    agent_submission: bool = True,
) -> dict[str, Any]:
    """Validate and copy one Claim.

    `agent_submission=True` is the fail-safe default.  Only the deterministic
    orchestrator may pass False when loading its own versioned snapshots.
    """
    raw = _require_object(payload, "claim")
    _require_exact_fields(raw, _CLAIM_FIELDS, "claim")
    claim_id = _clean_id(raw["id"], "claim.id")
    version = _clean_positive_int(raw["version"], "claim.version")
    kind = _clean_text(raw["kind"], "claim.kind", 32).lower()
    if kind not in correctness_policy.CLAIM_KINDS:
        raise ValueError(f"claim.kind is invalid: {kind!r}")
    status = _clean_text(raw["status"], "claim.status", 32).lower()
    if agent_submission:
        status = correctness_policy.require_agent_submittable_status(status)
    elif status not in correctness_policy.CLAIM_STATUSES:
        raise ValueError(f"claim.status is invalid: {status!r}")

    depends_on = _clean_id_list(raw["depends_on"], "claim.depends_on")
    if claim_id in depends_on:
        raise ValueError("claim cannot depend on itself")

    source = _require_object(raw["source"], "claim.source")
    _require_exact_fields(source, _SOURCE_FIELDS, "claim.source")
    policy_version = _clean_text(source["policy_version"], "claim.source.policy_version", 80)
    if policy_version != correctness_policy.CLAIM_LEDGER_POLICY_VERSION:
        raise ValueError("claim.source.policy_version does not match ledger policy")

    return {
        "id": claim_id,
        "version": version,
        "kind": kind,
        "statement": _clean_text(raw["statement"], "claim.statement", 2_000),
        "target_ids": _clean_id_list(raw["target_ids"], "claim.target_ids", allow_empty=False),
        "stage_ids": _clean_id_list(raw["stage_ids"], "claim.stage_ids"),
        "depends_on": depends_on,
        "conditions": _clean_text_list(raw["conditions"], "claim.conditions"),
        "obligation_ids": _clean_id_list(raw["obligation_ids"], "claim.obligation_ids"),
        "check_spec": _clean_json_object(raw["check_spec"], "claim.check_spec"),
        "status": status,
        "source": {
            "task_id": _clean_id(source["task_id"], "claim.source.task_id"),
            "input_fingerprint": _clean_fingerprint(source["input_fingerprint"], "claim.source.input_fingerprint"),
            "policy_version": policy_version,
        },
    }


def normalize_certificate(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _require_object(payload, "certificate")
    _require_exact_fields(raw, _CERTIFICATE_FIELDS, "certificate")
    verifier_kind = _clean_text(raw["verifier_kind"], "certificate.verifier_kind", 32).lower()
    if verifier_kind not in correctness_policy.CERTIFICATE_VERIFIER_KINDS:
        raise ValueError("certificate.verifier_kind is invalid")
    check_type = _clean_text(raw["check_type"], "certificate.check_type", 32).lower()
    if check_type not in correctness_policy.CHECK_TYPES:
        raise ValueError("certificate.check_type is invalid")
    verdict = _clean_text(raw["verdict"], "certificate.verdict", 32).lower()
    if verdict not in correctness_policy.CERTIFICATE_VERDICTS:
        raise ValueError("certificate.verdict is invalid")
    checks = _clean_text_list(
        raw["decisive_checks"],
        "certificate.decisive_checks",
        maximum_items=12,
        maximum_text=500,
    )
    if verdict == "pass" and not checks:
        raise ValueError("pass certificate requires at least one decisive check")

    identity = _require_object(raw["verifier_identity"], "certificate.verifier_identity")
    _require_exact_fields(identity, _VERIFIER_IDENTITY_FIELDS, "certificate.verifier_identity")
    if not isinstance(identity["context_isolated"], bool):
        raise ValueError("certificate.verifier_identity.context_isolated must be boolean")

    return {
        "claim_id": _clean_id(raw["claim_id"], "certificate.claim_id"),
        "claim_version": _clean_positive_int(raw["claim_version"], "certificate.claim_version"),
        "verifier_kind": verifier_kind,
        "check_type": check_type,
        "verdict": verdict,
        "normalized_result": structured_text.reject_unsupported_controls(
            raw["normalized_result"], "certificate.normalized_result"
        ).strip()[:2_000],
        "decisive_checks": checks,
        "input_fingerprint": _clean_fingerprint(raw["input_fingerprint"], "certificate.input_fingerprint"),
        "verifier_identity": {
            "model_id": _clean_text(
                identity["model_id"],
                "certificate.verifier_identity.model_id",
                120,
            ),
            "provider": _clean_text(
                identity["provider"],
                "certificate.verifier_identity.provider",
                80,
            ),
            "context_isolated": identity["context_isolated"],
        },
    }


def normalize_atomic_task(payload: dict[str, Any]) -> dict[str, Any]:
    raw = _require_object(payload, "task")
    _require_exact_fields(raw, _TASK_FIELDS, "task")
    action = _clean_text(raw["action"], "task.action", 64).lower()
    if action not in correctness_policy.TASK_ACTIONS:
        raise ValueError("task.action is invalid")
    status = _clean_text(raw["status"], "task.status", 32).lower()
    if status not in correctness_policy.TASK_STATUSES:
        raise ValueError("task.status is invalid")
    seed = raw["random_seed"]
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError("task.random_seed must be an integer or null")
    return {
        "task_id": _clean_id(raw["task_id"], "task.task_id"),
        "action": action,
        "target_ids": _clean_id_list(raw["target_ids"], "task.target_ids", allow_empty=False),
        "input_snapshot": _clean_positive_int(raw["input_snapshot"], "task.input_snapshot"),
        "input_fingerprint": _clean_fingerprint(raw["input_fingerprint"], "task.input_fingerprint"),
        "output_contract": _clean_text(raw["output_contract"], "task.output_contract", 120),
        "strategy": _clean_text(raw["strategy"], "task.strategy", 80),
        "random_seed": seed,
        "status": status,
    }


def normalize_graph_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the container contract; graph semantics are checked separately."""
    raw = _require_object(payload, "snapshot")
    _require_exact_fields(raw, _SNAPSHOT_FIELDS, "snapshot")
    if raw["schema_version"] != 1:
        raise ValueError("snapshot.schema_version must be 1")
    policy_version = _clean_text(raw["policy_version"], "snapshot.policy_version", 80)
    if policy_version != correctness_policy.CLAIM_LEDGER_POLICY_VERSION:
        raise ValueError("snapshot.policy_version does not match ledger policy")
    if not isinstance(raw["claims"], list):
        raise ValueError("snapshot.claims must be an array")
    if not isinstance(raw["certificates"], list):
        raise ValueError("snapshot.certificates must be an array")
    if not isinstance(raw["tasks"], list):
        raise ValueError("snapshot.tasks must be an array")
    claims = [normalize_claim(item, agent_submission=False) for item in raw["claims"]]
    certificates = [normalize_certificate(item) for item in raw["certificates"]]
    tasks = [normalize_atomic_task(item) for item in raw["tasks"]]
    claim_keys = [(item["id"], item["version"]) for item in claims]
    if len(claim_keys) != len(set(claim_keys)):
        raise ValueError("snapshot claims must have unique id/version pairs")
    task_ids = [item["task_id"] for item in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("snapshot tasks must have unique task_id values")
    logger.info(
        "stage=graph_snapshot status=normalized snapshot_version=%d claim_count=%d certificate_count=%d task_count=%d",
        raw["snapshot_version"],
        len(claims),
        len(certificates),
        len(tasks),
    )
    return {
        "schema_version": 1,
        "snapshot_version": _clean_positive_int(raw["snapshot_version"], "snapshot.snapshot_version"),
        "policy_version": policy_version,
        "input_fingerprint": _clean_fingerprint(raw["input_fingerprint"], "snapshot.input_fingerprint"),
        "claims": claims,
        "certificates": certificates,
        "tasks": tasks,
    }


def stable_fingerprint(namespace: str, payload: Any) -> str:
    """Create a replay-stable SHA-256 over finite JSON and a typed namespace."""
    normalized_namespace = _clean_text(namespace, "fingerprint namespace", 120)
    try:
        serialized = json.dumps(
            {
                "namespace": normalized_namespace,
                "payload": payload,
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("fingerprint payload must contain finite JSON values") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _claim_content(claim: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_claim(claim, agent_submission=False)
    return {
        "kind": normalized["kind"],
        "statement": normalized["statement"],
        "target_ids": sorted(normalized["target_ids"]),
        "stage_ids": sorted(normalized["stage_ids"]),
        "depends_on": sorted(normalized["depends_on"]),
        "conditions": sorted(normalized["conditions"]),
        "obligation_ids": sorted(normalized["obligation_ids"]),
        "check_spec": normalized["check_spec"],
    }


def claim_content_fingerprint(claim: dict[str, Any]) -> str:
    """Fingerprint claim semantics without identity, version, trust status, or source."""
    return stable_fingerprint("claim-content-v1", _claim_content(claim))


def claim_fingerprint(claim: dict[str, Any]) -> str:
    """Fingerprint one versioned Claim while excluding mutable trust status."""
    normalized = normalize_claim(claim, agent_submission=False)
    return stable_fingerprint(
        "claim-v1",
        {
            "id": normalized["id"],
            "version": normalized["version"],
            "policy_version": normalized["source"]["policy_version"],
            "content_fingerprint": claim_content_fingerprint(normalized),
        },
    )


def claim_verification_input_fingerprint(
    claim: dict[str, Any],
    dependency_claims: list[dict[str, Any]],
) -> str:
    """Bind a verifier to the current claim and exact upstream Claim versions."""
    normalized_claim = normalize_claim(claim, agent_submission=False)
    normalized_dependencies = [normalize_claim(item, agent_submission=False) for item in dependency_claims]
    expected_ids = set(normalized_claim["depends_on"])
    actual_ids = [item["id"] for item in normalized_dependencies]
    if len(actual_ids) != len(set(actual_ids)):
        raise ValueError("verification dependencies must have unique claim ids")
    if set(actual_ids) != expected_ids:
        raise ValueError("verification dependencies do not match claim.depends_on")
    return stable_fingerprint(
        "claim-verification-input-v1",
        {
            "policy_version": correctness_policy.CORRECTNESS_POLICY_VERSION,
            "claim_fingerprint": claim_fingerprint(normalized_claim),
            "dependency_fingerprints": [
                claim_fingerprint(item)
                for item in sorted(
                    normalized_dependencies,
                    key=lambda item: (item["id"], item["version"]),
                )
            ],
        },
    )


def atomic_task_fingerprint(task: dict[str, Any]) -> str:
    """Fingerprint task semantics; task id and mutable status are intentionally absent."""
    normalized = normalize_atomic_task(task)
    return stable_fingerprint(
        "atomic-task-v1",
        {
            "policy_version": correctness_policy.CORRECTNESS_POLICY_VERSION,
            "action": normalized["action"],
            "target_ids": sorted(normalized["target_ids"]),
            "input_snapshot": normalized["input_snapshot"],
            "input_fingerprint": normalized["input_fingerprint"],
            "output_contract": normalized["output_contract"],
            "strategy": normalized["strategy"],
            "random_seed": normalized["random_seed"],
        },
    )


def next_claim_version(claim_id: str, existing_claims: list[dict[str, Any]]) -> int:
    """Return the only valid next version number for a stable Claim identity."""
    normalized_id = _clean_id(claim_id, "claim_id")
    versions = [
        normalize_claim(item, agent_submission=False)["version"]
        for item in existing_claims
        if isinstance(item, dict) and str(item.get("id", "")).strip() == normalized_id
    ]
    return max(versions, default=0) + 1


def require_next_claim_version(
    candidate: dict[str, Any],
    existing_claims: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reject version gaps, overwrites, and no-op revisions."""
    normalized = normalize_claim(candidate)
    expected_version = next_claim_version(normalized["id"], existing_claims)
    if normalized["version"] != expected_version:
        raise ValueError(f"claim.version must be {expected_version} for {normalized['id']}")
    prior_versions = [
        normalize_claim(item, agent_submission=False)
        for item in existing_claims
        if isinstance(item, dict) and str(item.get("id", "")).strip() == normalized["id"]
    ]
    if prior_versions:
        latest = max(prior_versions, key=lambda item: item["version"])
        if claim_content_fingerprint(latest) == claim_content_fingerprint(normalized):
            raise ValueError("new claim version must change claim content")
    return normalized


def active_claims(claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resolve the one non-superseded version allowed for each Claim id."""
    if not isinstance(claims, list):
        raise ValueError("claims must be an array")
    resolved: dict[str, dict[str, Any]] = {}
    seen_versions: set[tuple[str, int]] = set()
    superseded_count = 0
    for raw in claims:
        claim = normalize_claim(raw, agent_submission=False)
        key = (claim["id"], claim["version"])
        if key in seen_versions:
            raise ValueError("claims must have unique id/version pairs")
        seen_versions.add(key)
        if claim["status"] == "superseded":
            superseded_count += 1
            continue
        if claim["id"] in resolved:
            raise ValueError(f"claim {claim['id']} has multiple non-superseded versions")
        resolved[claim["id"]] = claim
    if superseded_count:
        logger.info(
            "stage=active_claims resolved_count=%d superseded_count=%d",
            len(resolved),
            superseded_count,
        )
    return resolved


def topological_claim_ids(claims: list[dict[str, Any]]) -> list[str]:
    """Return a deterministic dependency-first order or reject an invalid DAG."""
    resolved = active_claims(claims)
    indegree = {claim_id: 0 for claim_id in resolved}
    downstream: dict[str, set[str]] = {claim_id: set() for claim_id in resolved}
    for claim_id, claim in resolved.items():
        for dependency_id in claim["depends_on"]:
            if dependency_id not in resolved:
                raise ValueError(f"claim {claim_id} has dangling dependency {dependency_id}")
            indegree[claim_id] += 1
            downstream[dependency_id].add(claim_id)

    ready = sorted(claim_id for claim_id, degree in indegree.items() if degree == 0)
    order: list[str] = []
    while ready:
        claim_id = ready.pop(0)
        order.append(claim_id)
        for child_id in sorted(downstream[claim_id]):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                ready.append(child_id)
                ready.sort()
    if len(order) != len(resolved):
        cyclic = sorted(claim_id for claim_id, degree in indegree.items() if degree > 0)
        raise ValueError(f"claim dependency graph contains a cycle: {cyclic}")
    return order


def validate_claim_graph(
    claims: list[dict[str, Any]],
    *,
    expected_target_ids: set[str],
    expected_obligation_ids: set[str],
) -> dict[str, Any]:
    """Validate DAG integrity and complete target/obligation projection."""
    if not expected_target_ids:
        raise ValueError("expected_target_ids must not be empty")
    resolved = active_claims(claims)
    order = topological_claim_ids(claims)

    observed_target_ids: set[str] = set()
    final_target_ids: set[str] = set()
    observed_obligation_ids: set[str] = set()
    for claim in resolved.values():
        observed_target_ids.update(claim["target_ids"])
        observed_obligation_ids.update(claim["obligation_ids"])
        if claim["kind"] == "final":
            final_target_ids.update(claim["target_ids"])

    unknown_targets = observed_target_ids - expected_target_ids
    if unknown_targets:
        raise ValueError(f"claims reference unknown targets: {sorted(unknown_targets)}")
    missing_final_targets = expected_target_ids - final_target_ids
    if missing_final_targets:
        raise ValueError(f"final claims do not cover targets: {sorted(missing_final_targets)}")
    unknown_obligations = observed_obligation_ids - expected_obligation_ids
    if unknown_obligations:
        raise ValueError(f"claims reference unknown obligations: {sorted(unknown_obligations)}")
    missing_obligations = expected_obligation_ids - observed_obligation_ids
    if missing_obligations:
        raise ValueError(f"claims do not cover obligations: {sorted(missing_obligations)}")

    return {
        "status": "valid",
        "active_claim_count": len(resolved),
        "topological_order": order,
        "target_coverage": {target_id: target_id in final_target_ids for target_id in sorted(expected_target_ids)},
        "obligation_coverage": {
            obligation_id: obligation_id in observed_obligation_ids for obligation_id in sorted(expected_obligation_ids)
        },
    }


def dependency_impact_cone(
    claims: list[dict[str, Any]],
    changed_claim_ids: set[str],
    *,
    include_changed: bool = False,
) -> list[str]:
    """Return only active downstream Claims affected by changed inputs."""
    resolved = active_claims(claims)
    unknown = changed_claim_ids - set(resolved)
    if unknown:
        raise ValueError(f"changed claims are unknown or superseded: {sorted(unknown)}")
    downstream: dict[str, set[str]] = {claim_id: set() for claim_id in resolved}
    for claim_id, claim in resolved.items():
        for dependency_id in claim["depends_on"]:
            if dependency_id not in resolved:
                raise ValueError(f"claim {claim_id} has dangling dependency {dependency_id}")
            downstream[dependency_id].add(claim_id)

    affected = set(changed_claim_ids) if include_changed else set()
    frontier = list(sorted(changed_claim_ids))
    visited = set(changed_claim_ids)
    while frontier:
        parent_id = frontier.pop(0)
        for child_id in sorted(downstream[parent_id]):
            affected.add(child_id)
            if child_id not in visited:
                visited.add(child_id)
                frontier.append(child_id)
    order = topological_claim_ids(claims)
    result = [claim_id for claim_id in order if claim_id in affected]
    logger.info(
        "stage=impact_cone changed_count=%d affected_count=%d",
        len(changed_claim_ids),
        len(result),
    )
    return result


def _legacy_projection_id(role: str, source_id: str, index: int = 0) -> str:
    digest = hashlib.sha256(f"{role}\0{source_id}\0{index}".encode()).hexdigest()[:12]
    return f"L-{role}-{digest}"


def project_legacy_solution(
    solution: dict[str, Any],
    blueprint: dict[str, Any],
    *,
    input_fingerprint: str,
    snapshot_version: int = 1,
) -> dict[str, Any]:
    """Project normalized solution-reasoning output into a G1 ledger.

    Free-text relations are preserved as Claims but explicitly marked
    `semantic-required`; projection compatibility is not treated as proof.
    """
    if not isinstance(solution, dict) or solution.get("status") != "completed":
        raise ValueError("only a completed normalized solution can be projected")
    if not isinstance(blueprint, dict):
        raise ValueError("blueprint must be an object")
    fingerprint = _clean_fingerprint(input_fingerprint, "input_fingerprint")
    target_ids = {
        _clean_id(item.get("id"), "blueprint.question_targets.id") for item in blueprint.get("question_targets", [])
    }
    obligation_ids = {
        _clean_id(item.get("id"), "blueprint.verification_obligations.id")
        for item in blueprint.get("verification_obligations", [])
    }
    if not target_ids:
        raise ValueError("blueprint must contain at least one question target")

    step_targets = {
        str(item.get("id", "")).strip(): {
            _clean_id(target_id, "blueprint.reasoning_steps.target_ids") for target_id in item.get("target_ids", [])
        }
        for item in blueprint.get("reasoning_steps", [])
        if isinstance(item, dict)
    }
    stage_targets: dict[str, set[str]] = {}
    for link in blueprint.get("stage_step_links", []):
        if not isinstance(link, dict):
            continue
        stage_id = str(link.get("stage_id", "")).strip()
        step_id = str(link.get("step_id", "")).strip()
        stage_targets.setdefault(stage_id, set()).update(step_targets.get(step_id, set()))

    claims: list[dict[str, Any]] = []
    stage_claim_ids_by_target: dict[str, list[str]] = {target_id: [] for target_id in target_ids}
    for stage_result in solution.get("stage_results", []):
        stage_id = _clean_id(stage_result.get("stage_id"), "solution.stage_results.stage_id")
        mapped_targets = sorted(stage_targets.get(stage_id, set()))
        if not mapped_targets:
            # Older blueprints may not contain enough linkage to make this
            # result an honest target-bound Claim.  It is intentionally omitted
            # rather than being attached to every target by guesswork.
            continue
        claim_id = _legacy_projection_id("S", stage_id)
        stage_claim = {
            "id": claim_id,
            "version": 1,
            "kind": "derived",
            "statement": _clean_text(
                stage_result.get("result"),
                "solution.stage_results.result",
                2_000,
            ),
            "target_ids": mapped_targets,
            "stage_ids": [stage_id],
            "depends_on": [],
            "conditions": [],
            "obligation_ids": [],
            "check_spec": {
                "type": "semantic-required",
                "reason": "legacy stage result has no atomic derivation",
                "source_contract": "wuli.solution-reasoning.v2.1",
            },
            "status": "candidate",
            "source": {
                "task_id": _legacy_projection_id("TS", stage_id),
                "input_fingerprint": fingerprint,
                "policy_version": correctness_policy.CLAIM_LEDGER_POLICY_VERSION,
            },
        }
        claims.append(normalize_claim(stage_claim))
        for target_id in mapped_targets:
            stage_claim_ids_by_target[target_id].append(claim_id)

    for target in solution.get("targets", []):
        target_id = _clean_id(target.get("id"), "solution.targets.id")
        if target_id not in target_ids:
            raise ValueError(f"solution target is unknown: {target_id}")
        relation_claim_ids: list[str] = []
        for index, relation in enumerate(target.get("supporting_relations", []), 1):
            claim_id = _legacy_projection_id("R", target_id, index)
            relation_claim = {
                "id": claim_id,
                "version": 1,
                "kind": "derived",
                "statement": _clean_text(
                    relation,
                    "solution.targets.supporting_relations",
                    2_000,
                ),
                "target_ids": [target_id],
                "stage_ids": [],
                "depends_on": sorted(stage_claim_ids_by_target[target_id]),
                "conditions": list(target.get("conditions", [])),
                "obligation_ids": [],
                "check_spec": {
                    "type": "semantic-required",
                    "reason": "legacy free-text relation is not machine-checkable",
                    "source_contract": "wuli.solution-reasoning.v2.1",
                },
                "status": "candidate",
                "source": {
                    "task_id": _legacy_projection_id("TR", target_id, index),
                    "input_fingerprint": fingerprint,
                    "policy_version": correctness_policy.CLAIM_LEDGER_POLICY_VERSION,
                },
            }
            claims.append(normalize_claim(relation_claim))
            relation_claim_ids.append(claim_id)
        if not relation_claim_ids:
            raise ValueError(f"solution target {target_id} has no supporting relations")

        final_claim = {
            "id": _legacy_projection_id("F", target_id),
            "version": 1,
            "kind": "final",
            "statement": _clean_text(target.get("final_answer"), "solution.targets.final_answer", 2_000),
            "target_ids": [target_id],
            "stage_ids": [],
            "depends_on": relation_claim_ids,
            "conditions": list(target.get("conditions", [])),
            "obligation_ids": list(target.get("covered_obligation_ids", [])),
            "check_spec": {
                "type": "aggregation",
                "mode": "legacy-projection",
                "semantic_required": True,
            },
            "status": "candidate",
            "source": {
                "task_id": _legacy_projection_id("TF", target_id),
                "input_fingerprint": fingerprint,
                "policy_version": correctness_policy.CLAIM_LEDGER_POLICY_VERSION,
            },
        }
        claims.append(normalize_claim(final_claim))

    snapshot = normalize_graph_snapshot({
        "schema_version": 1,
        "snapshot_version": snapshot_version,
        "policy_version": correctness_policy.CLAIM_LEDGER_POLICY_VERSION,
        "input_fingerprint": fingerprint,
        "claims": claims,
        "certificates": [],
        "tasks": [],
    })
    validate_claim_graph(
        snapshot["claims"],
        expected_target_ids=target_ids,
        expected_obligation_ids=obligation_ids,
    )
    logger.info(
        "stage=legacy_projection status=completed snapshot_version=%d claim_count=%d target_count=%d",
        snapshot_version,
        len(snapshot["claims"]),
        len(target_ids),
    )
    return snapshot
