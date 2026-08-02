#!/usr/bin/env python3
"""Frozen policy constants for the claim-level correctness evidence pipeline.

This module is deliberately free of provider calls and filesystem writes.  It
defines what the later ledger and validators are allowed to accept; it does not
decide whether any concrete physics claim is true.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CORRECTNESS_POLICY_VERSION = "correctness-policy-v1"
CLAIM_LEDGER_POLICY_VERSION = "claim-ledger-v1"
CLAIM_EVIDENCE_SHADOW_ENV = "TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW"
DEFAULT_CLAIM_EVIDENCE_SHADOW = False
CLAIM_VERIFY_CONCURRENCY_ENV = "TEACHER_CONSOLE_W3_CLAIM_VERIFY_CONCURRENCY"
DEFAULT_CLAIM_VERIFY_CONCURRENCY = 2
MAX_CLAIM_VERIFY_CONCURRENCY = 2

CLAIM_KINDS = (
    "premise",
    "model",
    "derived",
    "numerical",
    "boundary",
    "final",
)
CLAIM_STATUSES = (
    "candidate",
    "verified",
    "disputed",
    "unresolved",
    "superseded",
)
AGENT_SUBMITTABLE_CLAIM_STATUSES = frozenset({"candidate"})
ORCHESTRATOR_ONLY_CLAIM_STATUSES = frozenset(CLAIM_STATUSES) - AGENT_SUBMITTABLE_CLAIM_STATUSES

CERTIFICATE_VERIFIER_KINDS = (
    "source",
    "deterministic",
    "independent-agent",
    "teacher",
)
CERTIFICATE_VERDICTS = ("pass", "conflict", "insufficient", "unsupported")
CHECK_TYPES = (
    "source-match",
    "arithmetic",
    "dimension",
    "interval",
    "event-order",
    "semantic",
    "aggregation",
)

TASK_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "superseded",
)
TASK_ACTIONS = (
    "build_claim",
    "verify_claim",
    "diagnose_conflict",
    "generate_hypothesis",
    "aggregate_proof",
)

# A route is complete when at least one accepted verifier kind issues a passing
# certificate for one accepted check type.  Risk may add extra checks later, but
# can never remove the base route recorded here.
CERTIFICATE_REQUIREMENTS: dict[str, dict[str, tuple[str, ...]]] = {
    "premise": {
        "verifier_kinds": ("source", "teacher"),
        "check_types": ("source-match",),
    },
    "model": {
        "verifier_kinds": ("independent-agent", "teacher"),
        "check_types": ("semantic",),
    },
    "derived": {
        "verifier_kinds": ("deterministic", "independent-agent", "teacher"),
        "check_types": ("arithmetic", "dimension", "semantic"),
    },
    "numerical": {
        "verifier_kinds": ("deterministic", "independent-agent", "teacher"),
        "check_types": ("arithmetic", "dimension", "semantic"),
    },
    "boundary": {
        "verifier_kinds": ("deterministic", "independent-agent", "teacher"),
        "check_types": ("interval", "event-order", "semantic"),
    },
    "final": {
        "verifier_kinds": ("deterministic", "independent-agent", "teacher"),
        "check_types": ("aggregation", "semantic"),
    },
}

# Same-version claims never return from a terminal/problem state to candidate.
# A revised statement must be submitted as a new version and supersede the old
# claim, which prevents silent in-place rewrites of the proof graph.
CLAIM_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"verified", "disputed", "unresolved", "superseded"}),
    "verified": frozenset({"disputed", "superseded"}),
    "disputed": frozenset({"superseded"}),
    "unresolved": frozenset({"superseded"}),
    "superseded": frozenset(),
}

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def claim_evidence_shadow_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Read the shadow flag strictly and fail closed when it is absent."""
    if environ is None:
        import os

        environ = os.environ
    raw = environ.get(CLAIM_EVIDENCE_SHADOW_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_CLAIM_EVIDENCE_SHADOW
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(
        f"{CLAIM_EVIDENCE_SHADOW_ENV} must be one of "
        f"{sorted(_TRUE_VALUES | _FALSE_VALUES)}"
    )


def claim_verify_concurrency(environ: Mapping[str, str] | None = None) -> int:
    """Return validated Claim batch concurrency with a serial rollback setting."""
    if environ is None:
        import os

        environ = os.environ
    raw = environ.get(CLAIM_VERIFY_CONCURRENCY_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_CLAIM_VERIFY_CONCURRENCY
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"{CLAIM_VERIFY_CONCURRENCY_ENV} must be an integer from 1 to "
            f"{MAX_CLAIM_VERIFY_CONCURRENCY}"
        ) from exc
    if not 1 <= value <= MAX_CLAIM_VERIFY_CONCURRENCY:
        raise ValueError(
            f"{CLAIM_VERIFY_CONCURRENCY_ENV} must be an integer from 1 to "
            f"{MAX_CLAIM_VERIFY_CONCURRENCY}"
        )
    return value


def require_agent_submittable_status(status: str) -> str:
    """Reject any attempt by an Agent candidate to self-assign a trusted state."""
    normalized = str(status).strip().lower()
    if normalized not in AGENT_SUBMITTABLE_CLAIM_STATUSES:
        raise ValueError("Agent-submitted claim status must be candidate")
    return normalized


def can_transition_claim_status(current: str, target: str) -> bool:
    current_value = str(current).strip().lower()
    target_value = str(target).strip().lower()
    if current_value not in CLAIM_STATUS_TRANSITIONS or target_value not in CLAIM_STATUSES:
        return False
    return target_value in CLAIM_STATUS_TRANSITIONS[current_value]


def require_claim_status_transition(current: str, target: str) -> str:
    """Return the normalized target status or reject an illegal mutation."""
    normalized = str(target).strip().lower()
    if not can_transition_claim_status(current, normalized):
        raise ValueError(f"illegal claim status transition: {current!r} -> {target!r}")
    return normalized


def certificate_requirement(claim_kind: str) -> dict[str, tuple[str, ...]]:
    """Return a copy of the fail-closed base certificate route for one Claim."""
    normalized = str(claim_kind).strip().lower()
    requirement = CERTIFICATE_REQUIREMENTS.get(normalized)
    if requirement is None:
        raise ValueError(f"unknown claim kind: {claim_kind!r}")
    return {key: tuple(value) for key, value in requirement.items()}


def policy_snapshot() -> dict[str, Any]:
    """Return the JSON-serializable policy surface used in report fingerprints."""
    return {
        "policy_version": CORRECTNESS_POLICY_VERSION,
        "ledger_policy_version": CLAIM_LEDGER_POLICY_VERSION,
        "claim_kinds": list(CLAIM_KINDS),
        "claim_statuses": list(CLAIM_STATUSES),
        "certificate_requirements": {
            kind: {
                key: list(value)
                for key, value in CERTIFICATE_REQUIREMENTS[kind].items()
            }
            for kind in CLAIM_KINDS
        },
        "shadow_env": CLAIM_EVIDENCE_SHADOW_ENV,
        "shadow_default": DEFAULT_CLAIM_EVIDENCE_SHADOW,
        "claim_verify_concurrency_env": CLAIM_VERIFY_CONCURRENCY_ENV,
        "claim_verify_concurrency_default": DEFAULT_CLAIM_VERIFY_CONCURRENCY,
        "claim_verify_concurrency_max": MAX_CLAIM_VERIFY_CONCURRENCY,
    }
