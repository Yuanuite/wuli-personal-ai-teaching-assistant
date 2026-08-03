#!/usr/bin/env python3
"""Deterministic whole-proof aggregation for teacher review and trust labeling."""

from __future__ import annotations

from typing import Any

import claim_ledger
import claim_validation
import cognitive_loop
from log import TraceContext, get_logger

logger = get_logger("proof_aggregation")

AGGREGATION_POLICY = "wuli.proof-aggregation.v1"


def _root_path_issues(
    claims: dict[str, dict[str, Any]],
) -> list[str]:
    issues: list[str] = []
    for claim_id, claim in claims.items():
        if not claim["depends_on"] and claim["kind"] != "premise":
            issues.append(f"non-premise root {claim_id} has no dependency path to approved facts")
        if claim["kind"] == "premise" and claim["depends_on"]:
            issues.append(f"premise {claim_id} must not depend on derived Claims")
    return issues


def aggregate_proof(
    claims: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
    *,
    expected_target_ids: set[str],
    expected_obligation_ids: set[str],
    interface_report: dict[str, Any],
    challenges: list[dict[str, Any]] | None = None,
    hypotheses: list[dict[str, Any]] | None = None,
    risks: dict[str, str] | None = None,
    generator_identities: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Always expose complete final candidates; add VERIFIED only when earned."""
    trace_id = f"aggregate-{claim_ledger.stable_fingerprint('proof-aggregate-input-v1', {'targets': sorted(expected_target_ids)})[:16]}"
    with TraceContext(trace_id, log=logger) as ctx:
        ctx.info(
            "stage=aggregate_proof status=started claim_count=%d certificate_count=%d target_count=%d",
            len(claims),
            len(certificates),
            len(expected_target_ids),
        )
    evidence = claim_validation.evaluate_claim_graph_evidence(
        claims,
        certificates,
        expected_target_ids=expected_target_ids,
        expected_obligation_ids=expected_obligation_ids,
        risks=risks,
        generator_identities=generator_identities,
    )
    trusted = {item["id"]: item for item in evidence["claims"]}
    final_claims = [
        {
            "claim_id": item["id"],
            "claim_version": item["version"],
            "target_ids": item["target_ids"],
            "statement": item["statement"],
            "conditions": item["conditions"],
            "status": item["status"],
            "obligation_ids": item["obligation_ids"],
        }
        for item in evidence["claims"]
        if item["kind"] == "final"
    ]
    if not final_claims:
        raise ValueError("proof aggregation requires at least one final Claim")

    interface_status = str(interface_report.get("status", "")).strip().lower()
    if interface_status not in {"pass", "provisional", "conflict"}:
        raise ValueError("interface report status is invalid")
    interface_issues = interface_report.get("issues")
    if not isinstance(interface_issues, list):
        raise ValueError("interface report issues must be an array")

    normalized_challenges = [
        cognitive_loop.normalize_challenge_ticket(
            item,
            available_claim_ids=set(trusted),
            agent_submission=False,
        )
        for item in (challenges or [])
    ]
    normalized_hypotheses = [
        cognitive_loop.normalize_hypothesis(
            item,
            available_claim_ids=set(trusted),
            agent_submission=False,
        )
        for item in (hypotheses or [])
    ]
    active_challenges = [item for item in normalized_challenges if item["status"] in {"candidate", "open"}]
    active_hypotheses = [item for item in normalized_hypotheses if item["status"] not in {"rejected", "superseded"}]
    path_issues = _root_path_issues(trusted)
    issues: list[str] = list(path_issues)
    if evidence["result_status"] != "VERIFIED":
        issues.append(f"claim evidence graph is {evidence['result_status']}")
    if interface_status != "pass":
        issues.append(f"stage interface report is {interface_status}")
    if active_challenges:
        issues.append(f"{len(active_challenges)} challenge(s) remain open")
    if active_hypotheses:
        issues.append(f"{len(active_hypotheses)} hypothesis candidate(s) remain active")

    hard_conflict = (
        evidence["result_status"] == "UNRESOLVED"
        or interface_status == "conflict"
        or any(
            item["trigger"]
            in {
                "verification-conflict",
                "interface-mismatch",
            }
            for item in active_challenges
        )
    )
    if hard_conflict:
        status = "UNRESOLVED"
    elif issues:
        status = "PROVISIONAL"
    else:
        status = "VERIFIED"

    logger.info(
        "stage=aggregate_proof status=%s final_claim_count=%d issue_count=%d open_challenge_count=%d",
        status,
        len(final_claims),
        len(issues),
        len(active_challenges),
    )
    return {
        "schema_version": 1,
        "policy": AGGREGATION_POLICY,
        "status": status,
        "final_claims": final_claims,
        "teacher_review_answer_available": True,
        "verified_answer_render_allowed": status == "VERIFIED",
        "student_verified_delivery_allowed": False,
        "claim_evidence": evidence,
        "interface_status": interface_status,
        "interface_issues": interface_issues,
        "open_challenge_ids": [item["id"] for item in active_challenges],
        "active_hypothesis_ids": [item["id"] for item in active_hypotheses],
        "root_path_issues": path_issues,
        "issues": issues,
    }
