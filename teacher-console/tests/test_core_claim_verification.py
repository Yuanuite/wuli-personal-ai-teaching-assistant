#!/usr/bin/env python3
"""Work-tree D3: core claims → ledger projection and the promotion gate."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import claim_ledger  # noqa: E402
import correctness_policy  # noqa: E402
import w3_pipeline  # noqa: E402

FINGERPRINT = "a" * 64


def core_claim(claim_id: str, answer: str = "临界磁感应强度 B*=mv/(qd)") -> dict:
    return {
        "id": claim_id,
        "final_answer": answer,
        "key_relations": ["由 qvB=mv²/r 得到半径关系", "相切条件 r=d"],
    }


def test_project_core_claims_produces_valid_ledger_claims() -> None:
    projected = claim_ledger.project_core_claims(
        [core_claim("q1"), core_claim("q2")],
        task_id="core-solve",
        input_fingerprint=FINGERPRINT,
        source_contract="wuli.core-rich.v2",
    )
    assert [item["id"] for item in projected] == ["q1", "q2"]
    for item in projected:
        assert item["kind"] == "final"
        assert item["status"] == "candidate"
        assert item["target_ids"] == ["approved-problem"]
        assert item["stage_ids"] == ["core-solve"]
        assert item["depends_on"] == []
        assert item["conditions"], "key_relations must carry over as conditions"
        assert item["check_spec"]["type"] == "semantic-required"
        assert item["source"]["task_id"] == "core-solve"
        assert item["source"]["input_fingerprint"] == FINGERPRINT
        assert item["source"]["policy_version"] == correctness_policy.CLAIM_LEDGER_POLICY_VERSION


def test_project_core_claims_rejects_invalid_input() -> None:
    with pytest.raises(ValueError):
        claim_ledger.project_core_claims(
            [], task_id="core-solve", input_fingerprint=FINGERPRINT, source_contract="wuli.core-rich.v2"
        )
    with pytest.raises(ValueError):
        claim_ledger.project_core_claims(
            [core_claim("q1"), core_claim("q1")],
            task_id="core-solve",
            input_fingerprint=FINGERPRINT,
            source_contract="wuli.core-rich.v2",
        )
    with pytest.raises(ValueError):
        claim_ledger.project_core_claims(
            [core_claim("q1")],
            task_id="core-solve",
            input_fingerprint="not-a-fingerprint",
            source_contract="wuli.core-rich.v2",
        )


def certificate(claim_id: str, verdict: str, version: int = 1) -> dict:
    return {"claim_id": claim_id, "claim_version": version, "verdict": verdict}


def test_evaluate_claim_verification_canonical_when_every_claim_passes() -> None:
    claims = claim_ledger.project_core_claims(
        [core_claim("q1"), core_claim("q2")],
        task_id="core-solve",
        input_fingerprint=FINGERPRINT,
        source_contract="wuli.core-rich.v2",
    )
    result = claim_ledger.evaluate_claim_verification(
        claims, [certificate("q1", "pass"), certificate("q2", "pass")]
    )
    assert result["answer_status"] == "canonical"
    assert result["teacher_adjudication"] == []


def test_evaluate_claim_verification_provisional_on_conflict_or_missing() -> None:
    claims = claim_ledger.project_core_claims(
        [core_claim("q1"), core_claim("q2")],
        task_id="core-solve",
        input_fingerprint=FINGERPRINT,
        source_contract="wuli.core-rich.v2",
    )
    conflicted = claim_ledger.evaluate_claim_verification(
        claims, [certificate("q1", "pass"), certificate("q2", "conflict")]
    )
    assert conflicted["answer_status"] == "provisional"
    assert conflicted["teacher_adjudication"] == [
        {"claim_id": "q2", "claim_version": 1, "reason": "missing-or-non-pass-certificate"}
    ]
    missing = claim_ledger.evaluate_claim_verification(claims, [certificate("q1", "pass")])
    assert missing["answer_status"] == "provisional"
    assert [item["claim_id"] for item in missing["teacher_adjudication"]] == ["q2"]
    empty = claim_ledger.evaluate_claim_verification([], [])
    assert empty["answer_status"] == "provisional"


def test_run_claim_verification_batches_splits_and_materializes_certificates() -> None:
    claims = claim_ledger.project_core_claims(
        [core_claim(f"q{index}") for index in range(1, 10)],
        task_id="core-solve",
        input_fingerprint=FINGERPRINT,
        source_contract="wuli.core-rich.v2",
    )
    requests = [{"claim": item, "dependencies": []} for item in claims]
    source_facts = [{"id": "approved-problem", "statement": "题目", "conditions": []}]
    seen_batches: list[int] = []

    def stage_runner(stage: str, context: dict) -> dict:
        assert stage == "claim-verifier"
        view = context["verification_view"]
        seen_batches.append(context["batch_index"])
        assert "stage_interface_view" not in view, "core path must not leak W3 stage interfaces"
        return {
            "status": "completed",
            "message": "stub audit",
            "claim_audits": [
                {
                    "claim_id": request["claim"]["id"],
                    "claim_version": request["claim"]["version"],
                    "verdict": "pass",
                    "normalized_result": "independently recomputed",
                    "decisive_checks": ["stub decisive check"],
                    "issues": [],
                }
                for request in view["requests"]
            ],
            "interface_audit": None,
            "_runtime_identity": {
                "model_id": "stub-verifier",
                "provider": "claude",
                "context_isolated": True,
            },
        }

    audit_batches, certificates, concurrency, duration = w3_pipeline.run_claim_verification_batches(
        requests,
        source_facts,
        stage_runner,
        claim_verifier_concurrency=1,
        log_prefix="core_claim_verify_test",
    )
    assert seen_batches == [0, 1], "nine claims must split into two 8-wide batches"
    assert len(audit_batches) == 2
    assert concurrency == 1
    assert duration >= 0.0
    assert {item["claim_id"] for item in certificates} == {f"q{index}" for index in range(1, 10)}
    for item in certificates:
        assert item["verdict"] == "pass"
        assert item["verifier_identity"]["model_id"] == "stub-verifier"
        assert item["verifier_identity"]["context_isolated"] is True
