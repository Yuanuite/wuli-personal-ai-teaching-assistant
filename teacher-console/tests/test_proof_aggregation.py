import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import claim_ledger  # noqa: E402
import claim_validation  # noqa: E402
import proof_aggregation  # noqa: E402


def claim(claim_id, kind, depends_on=None, obligation_ids=None):
    check_spec = {
        "premise": None,
        "numerical": {
            "type": "arithmetic",
            "variables": {},
            "relations": [{
                "left": "2 * 3",
                "operator": "==",
                "right": "6",
            }],
        },
        "final": {"type": "aggregation"},
    }[kind]
    return {
        "id": claim_id,
        "version": 1,
        "kind": kind,
        "statement": {
            "premise": "The source gives a constant force.",
            "numerical": "The recomputed value is 6.",
            "final": "The final answer is 6.",
        }[kind],
        "target_ids": ["Q1"],
        "stage_ids": [],
        "depends_on": depends_on or [],
        "conditions": [],
        "obligation_ids": obligation_ids or [],
        "check_spec": check_spec,
        "status": "candidate",
        "source": {
            "task_id": f"build-{claim_id}",
            "input_fingerprint": "1" * 64,
            "policy_version": "claim-ledger-v1",
        },
    }


def fixture():
    premise = claim("C0", "premise")
    numerical = claim("C1", "numerical", ["C0"])
    final = claim("C2", "final", ["C1"], ["V1"])
    source_certificate = {
        "claim_id": "C0",
        "claim_version": 1,
        "verifier_kind": "source",
        "check_type": "source-match",
        "verdict": "pass",
        "normalized_result": "source S1",
        "decisive_checks": ["exact approved source statement"],
        "input_fingerprint": claim_ledger.claim_verification_input_fingerprint(
            premise, []
        ),
        "verifier_identity": {
            "model_id": "source-review",
            "provider": "local",
            "context_isolated": True,
        },
    }
    numerical_certificate = claim_validation.verify_arithmetic_claim(
        numerical, [premise]
    )
    final_certificate = {
        "claim_id": "C2",
        "claim_version": 1,
        "verifier_kind": "deterministic",
        "check_type": "aggregation",
        "verdict": "pass",
        "normalized_result": "all dependencies verified",
        "decisive_checks": ["C1 is verified and covers V1"],
        "input_fingerprint": claim_ledger.claim_verification_input_fingerprint(
            final, [numerical]
        ),
        "verifier_identity": {
            "model_id": "proof-aggregation-v1",
            "provider": "local",
            "context_isolated": True,
        },
    }
    return (
        [premise, numerical, final],
        [source_certificate, numerical_certificate, final_certificate],
    )


def interface_report(status="pass"):
    return {
        "status": status,
        "issues": [] if status == "pass" else [{"code": "state-value-mismatch"}],
    }


class ProofAggregationTest(unittest.TestCase):
    def test_complete_proof_is_verified_but_not_auto_delivered(self):
        claims, certificates = fixture()
        report = proof_aggregation.aggregate_proof(
            claims,
            certificates,
            expected_target_ids={"Q1"},
            expected_obligation_ids={"V1"},
            interface_report=interface_report(),
        )
        self.assertEqual(report["status"], "VERIFIED")
        self.assertTrue(report["teacher_review_answer_available"])
        self.assertTrue(report["verified_answer_render_allowed"])
        self.assertFalse(report["student_verified_delivery_allowed"])
        self.assertEqual(report["final_claims"][0]["statement"], "The final answer is 6.")

    def test_missing_evidence_keeps_full_answer_visible_for_teacher(self):
        claims, certificates = fixture()
        report = proof_aggregation.aggregate_proof(
            claims,
            certificates[:-1],
            expected_target_ids={"Q1"},
            expected_obligation_ids={"V1"},
            interface_report=interface_report(),
        )
        self.assertEqual(report["status"], "PROVISIONAL")
        self.assertTrue(report["teacher_review_answer_available"])
        self.assertFalse(report["verified_answer_render_allowed"])
        self.assertEqual(len(report["final_claims"]), 1)

    def test_interface_conflict_is_unresolved(self):
        claims, certificates = fixture()
        report = proof_aggregation.aggregate_proof(
            claims,
            certificates,
            expected_target_ids={"Q1"},
            expected_obligation_ids={"V1"},
            interface_report=interface_report("conflict"),
        )
        self.assertEqual(report["status"], "UNRESOLVED")

    def test_non_premise_root_blocks_verified_even_with_certificates(self):
        claims, certificates = fixture()
        claims = claims[1:]
        claims[0]["depends_on"] = []
        certificates = certificates[1:]
        certificates[0] = claim_validation.verify_arithmetic_claim(claims[0], [])
        certificates[1]["input_fingerprint"] = (
            claim_ledger.claim_verification_input_fingerprint(
                claims[1], [claims[0]]
            )
        )
        report = proof_aggregation.aggregate_proof(
            claims,
            certificates,
            expected_target_ids={"Q1"},
            expected_obligation_ids={"V1"},
            interface_report=interface_report(),
        )
        self.assertEqual(report["status"], "PROVISIONAL")
        self.assertTrue(report["root_path_issues"])

    def test_open_challenge_and_active_hypothesis_block_verified(self):
        claims, certificates = fixture()
        challenge = {
            "id": "CH1",
            "snapshot_version": 1,
            "trigger": "risk-audit",
            "claim_ids": ["C1", "C2"],
            "specific_doubt": "The current solution may omit another valid branch.",
            "falsification_test": "Enumerate every admissible sign and boundary branch.",
            "suggested_backjump": "C1",
            "status": "open",
        }
        hypothesis = {
            "id": "H1",
            "snapshot_version": 1,
            "challenge_id": "CH1",
            "operator": "hidden-degree-of-freedom",
            "proposal": "A second initial sign may satisfy every condition.",
            "explains_gap": "It explains a possible missing all-solutions branch.",
            "novelty_basis": "No current Claim varies the initial sign.",
            "falsification": {
                "test_type": "deterministic",
                "procedure": "Enumerate both signs and propagate each branch.",
                "expected_observation": "The second sign reaches the target state.",
                "failure_observation": "The second sign violates a source condition.",
            },
            "affected_claim_ids": ["C1", "C2"],
            "status": "testing",
        }
        report = proof_aggregation.aggregate_proof(
            claims,
            certificates,
            expected_target_ids={"Q1"},
            expected_obligation_ids={"V1"},
            interface_report=interface_report(),
            challenges=[challenge],
            hypotheses=[hypothesis],
        )
        self.assertEqual(report["status"], "PROVISIONAL")
        self.assertEqual(report["open_challenge_ids"], ["CH1"])
        self.assertEqual(report["active_hypothesis_ids"], ["H1"])


if __name__ == "__main__":
    unittest.main()
