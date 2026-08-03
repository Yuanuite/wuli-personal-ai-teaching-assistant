#!/usr/bin/env python3
"""Deterministic correctness fault fixtures and reusable execution harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import claim_ledger
import claim_validation
import cognitive_loop
from log import TraceContext, get_logger

logger = get_logger("correctness_faults")

FAULT_POLICY = "wuli.correctness-faults.v1"
FINGERPRINT = "f" * 64


def load_fault_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != FAULT_POLICY:
        raise ValueError("correctness fault fixture schema is invalid")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("correctness fault fixture cases must not be empty")
    ids = [str(item.get("id", "")).strip() for item in cases]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("correctness fault fixture ids must be non-empty and unique")
    return cases


def _claim(
    *,
    claim_id: str = "C1",
    kind: str = "derived",
    depends_on: list[str] | None = None,
    check_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": claim_id,
        "version": 1,
        "kind": kind,
        "statement": f"Fault-injection Claim {claim_id}",
        "target_ids": ["Q1"],
        "stage_ids": [],
        "depends_on": depends_on or [],
        "conditions": [],
        "obligation_ids": [],
        "check_spec": check_spec,
        "status": "candidate",
        "source": {
            "task_id": f"build-{claim_id}",
            "input_fingerprint": FINGERPRINT,
            "policy_version": "claim-ledger-v1",
        },
    }


def _stage(stage_id: str, *, entry_x: str, exit_x: str) -> dict[str, Any]:
    return {
        "stage_id": stage_id,
        "coordinate_frame": "laboratory",
        "time_origin": "problem-t0",
        "directions": {"x": "right", "y": "up"},
        "entry_state": {"x": entry_x, "speed": "v0"},
        "exit_state": {"x": exit_x, "speed": "v0"},
        "required_entry_keys": ["x", "speed"],
        "carried_state_keys": ["speed"],
    }


def _transition() -> dict[str, Any]:
    return {
        "from_stage": "P1",
        "to_stage": "P2",
        "event": "cross boundary",
        "state_mapping": [
            {"from_key": "x", "to_key": "x", "transform": None},
            {"from_key": "speed", "to_key": "speed", "transform": None},
        ],
        "introduced_entry_keys": [],
        "coordinate_transform": None,
        "time_transform": None,
        "direction_transform": None,
    }


def _semantic_certificate(
    claim: dict[str, Any],
    *,
    model_id: str,
    provider: str = "fixture",
) -> dict[str, Any]:
    return {
        "claim_id": claim["id"],
        "claim_version": claim["version"],
        "verifier_kind": "independent-agent",
        "check_type": "semantic",
        "verdict": "pass",
        "normalized_result": "pass",
        "decisive_checks": ["isolated recomputation"],
        "input_fingerprint": claim_ledger.claim_verification_input_fingerprint(
            claim, []
        ),
        "verifier_identity": {
            "model_id": model_id,
            "provider": provider,
            "context_isolated": True,
        },
    }


def _hypothesis_fixture() -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
]:
    challenge = {
        "id": "CH1",
        "snapshot_version": 2,
        "trigger": "risk-audit",
        "claim_ids": ["C1", "C2"],
        "specific_doubt": "The current solution may omit a direction branch.",
        "falsification_test": "Enumerate both directions and compare outcomes.",
        "suggested_backjump": "C1",
        "status": "candidate",
    }
    hypothesis = {
        "id": "H1",
        "snapshot_version": 2,
        "challenge_id": "CH1",
        "operator": "hidden-degree-of-freedom",
        "proposal": "The velocity sign creates a second admissible branch.",
        "explains_gap": "It explains why the all-solutions Claim has one branch.",
        "novelty_basis": "No current Claim varies the velocity direction.",
        "falsification": {
            "test_type": "deterministic",
            "procedure": "Enumerate both signs and propagate each state.",
            "expected_observation": "A second sign satisfies every source condition.",
            "failure_observation": "The second sign violates a source condition.",
        },
        "affected_claim_ids": ["C1", "C2"],
        "status": "candidate",
    }
    claims = [_claim(claim_id="C1"), _claim(claim_id="C2", depends_on=["C1"])]
    return challenge, hypothesis, claims


def run_fault_case(case: dict[str, Any]) -> dict[str, Any]:
    """Execute one frozen fault without provider calls or shared-state writes."""
    case_id = str(case.get("id", ""))
    category = str(case.get("category", ""))
    expected = str(case.get("expected", ""))
    detail: dict[str, Any] = {}
    logger.info("stage=fault_case status=started case_id=%s category=%s", case_id, category)

    if category in {"arithmetic", "dimension", "interval", "event-order"}:
        kind = {
            "arithmetic": "numerical",
            "dimension": "derived",
            "interval": "boundary",
            "event-order": "boundary",
        }[category]
        claim = _claim(kind=kind, check_spec=case["check_spec"])
        verifier = {
            "arithmetic": claim_validation.verify_arithmetic_claim,
            "dimension": claim_validation.verify_dimension_claim,
            "interval": claim_validation.verify_interval_claim,
            "event-order": claim_validation.verify_event_order_claim,
        }[category]
        certificate = verifier(claim, [])
        observed = certificate["verdict"]
        false_promotion = observed == "pass"
        detail = {
            "claim_count": 1,
            "certificate_count": 1,
            "decisive_check_count": len(certificate["decisive_checks"]),
        }
    elif category == "interface":
        first = _stage("P1", entry_x="0", exit_x="L")
        second = _stage("P2", entry_x="L", exit_x="2L")
        if case["mutation"] == "coordinate-and-direction":
            second["coordinate_frame"] = "moving-frame"
            second["directions"]["x"] = "left"
        elif case["mutation"] == "carried-speed":
            first["exit_state"]["speed"] = "2v0"
        else:
            raise ValueError(f"unknown interface mutation: {case['mutation']}")
        interface_report = cognitive_loop.check_stage_interfaces(
            [first, second], [_transition()]
        )
        observed = interface_report["status"]
        false_promotion = observed == "pass"
        detail = {
            "interface_issue_count": len(interface_report["issues"]),
            "interface_issue_codes": sorted({
                item["code"] for item in interface_report["issues"]
            }),
        }
    elif case_id == "F10-two-agents-same-error":
        claim = _claim(kind="numerical", check_spec=case["check_spec"])
        certificates = [claim_validation.verify_arithmetic_claim(claim, [])]
        certificates.extend(
            _semantic_certificate(claim, model_id=f"agent-{index}")
            for index in range(int(case["semantic_passes"]))
        )
        decision = claim_validation.apply_certificate_decision(
            claim, [], certificates, risk="critical"
        )
        observed = decision["claim"]["status"]
        false_promotion = observed == "verified"
        detail = {
            "certificate_count": len(certificates),
            "conflict_count": decision["assessment"]["conflict_count"],
        }
    elif case_id == "F11-repeated-hypothesis":
        challenge, hypothesis, claims = _hypothesis_fixture()
        first = cognitive_loop.add_hypothesis_to_pool(
            hypothesis, challenge, claims, []
        )
        repeated = cognitive_loop.add_hypothesis_to_pool(
            hypothesis, challenge, claims, first["pool"]
        )
        observed = repeated["decision"]
        false_promotion = len(repeated["pool"]) != 1
        detail = {
            "hypothesis_attempt_count": 2,
            "unique_hypothesis_count": len(repeated["pool"]),
            "duplicate_hypothesis_count": 1,
        }
    elif case_id == "F12-backjump-collateral":
        claims = [
            _claim(claim_id="C1"),
            _claim(claim_id="C2", depends_on=["C1"]),
            _claim(claim_id="C3", depends_on=["C2"]),
            _claim(claim_id="C4"),
            _claim(claim_id="C5", depends_on=["C4"]),
        ]
        result = cognitive_loop.invalidate_dependency_cone(
            claims,
            [],
            root_claim_ids=set(case["root_claim_ids"]),
            snapshot_version=1,
            challenge_id="CH1",
        )
        actual = set(result["affected_claim_ids"])
        expected_affected = set(case["expected_affected_claim_ids"])
        precision = (
            len(actual & expected_affected) / len(actual) if actual else 0.0
        )
        recall = (
            len(actual & expected_affected) / len(expected_affected)
            if expected_affected
            else 0.0
        )
        isolated = (
            result["affected_claim_ids"]
            == case["expected_affected_claim_ids"]
            and result["preserved_claim_ids"]
            == case["expected_preserved_claim_ids"]
        )
        observed = "isolated" if isolated else "collateral"
        false_promotion = not isolated
        detail = {
            "backjump_precision": round(precision, 6),
            "backjump_recall": round(recall, 6),
            "affected_claim_ids": result["affected_claim_ids"],
            "preserved_claim_ids": result["preserved_claim_ids"],
        }
    elif case_id == "F13-generator-self-verification":
        claim = _claim(
            kind="model", check_spec={"type": "semantic-required"}
        )
        certificate = _semantic_certificate(
            claim, model_id="solver", provider="same-runtime"
        )
        decision = claim_validation.apply_certificate_decision(
            claim,
            [],
            [certificate],
            generator_identity={
                "model_id": "solver",
                "provider": "same-runtime",
            },
        )
        observed = decision["claim"]["status"]
        false_promotion = observed == "verified"
        detail = {
            "self_certificate_count": decision["assessment"][
                "self_certificate_count"
            ],
        }
    elif case_id == "F14-hard-fuse-false-promotion":
        control = cognitive_loop.initial_loop_control()
        control["transition_count"] = 2
        result = cognitive_loop.advance_loop_control(
            control,
            {"progressed": True},
            evidence_status="UNRESOLVED",
            has_open_conflict=True,
            policy={
                "stagnation_before_strategy_change": 2,
                "strategy_count": 3,
                "max_transitions": 3,
            },
        )
        observed = result["control"]["terminal_status"]
        false_promotion = observed == "VERIFIED"
        detail = {
            "action": result["action"],
            "transition_count": result["control"]["transition_count"],
        }
    else:
        raise ValueError(f"unhandled correctness fault: {case_id}")

    logger.info(
        "stage=fault_case status=completed case_id=%s detected=%s false_promotion=%s",
        case_id,
        observed == expected and not false_promotion,
        false_promotion,
    )
    return {
        "case_id": case_id,
        "category": category,
        "fault": str(case.get("fault", "")),
        "detector": str(case.get("detector", "")),
        "expected": expected,
        "observed": observed,
        "detected": observed == expected and not false_promotion,
        "false_promotion": false_promotion,
        "detail": detail,
    }


def run_fault_suite(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with TraceContext(log=logger) as ctx:
        ctx.info("stage=fault_suite status=started case_count=%d", len(cases))
        results = [run_fault_case(item) for item in cases]
        detected_count = sum(1 for item in results if item["detected"])
        false_promotion_count = sum(1 for item in results if item["false_promotion"])
        ctx.info(
            "stage=fault_suite status=completed case_count=%d detected=%d false_promotion=%d",
            len(results),
            detected_count,
            false_promotion_count,
        )
    return results
