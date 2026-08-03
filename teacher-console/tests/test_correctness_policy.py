import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import correctness_policy  # noqa: E402


class CorrectnessPolicyTest(unittest.TestCase):
    def test_shadow_feature_is_off_by_default_and_parsed_strictly(self):
        self.assertFalse(correctness_policy.claim_evidence_shadow_enabled({}))
        self.assertTrue(
            correctness_policy.claim_evidence_shadow_enabled({correctness_policy.CLAIM_EVIDENCE_SHADOW_ENV: "true"})
        )
        self.assertFalse(
            correctness_policy.claim_evidence_shadow_enabled({correctness_policy.CLAIM_EVIDENCE_SHADOW_ENV: "OFF"})
        )
        with self.assertRaisesRegex(ValueError, "must be one of"):
            correctness_policy.claim_evidence_shadow_enabled({
                correctness_policy.CLAIM_EVIDENCE_SHADOW_ENV: "sometimes"
            })

    def test_every_claim_kind_has_a_nonempty_certificate_route(self):
        self.assertEqual(
            set(correctness_policy.CLAIM_KINDS),
            set(correctness_policy.CERTIFICATE_REQUIREMENTS),
        )
        for kind in correctness_policy.CLAIM_KINDS:
            requirement = correctness_policy.certificate_requirement(kind)
            self.assertTrue(requirement["verifier_kinds"])
            self.assertTrue(requirement["check_types"])
            self.assertTrue(set(requirement["verifier_kinds"]) <= set(correctness_policy.CERTIFICATE_VERIFIER_KINDS))
            self.assertTrue(set(requirement["check_types"]) <= set(correctness_policy.CHECK_TYPES))

    def test_claim_verifier_concurrency_defaults_to_validated_parallelism(self):
        env = correctness_policy.CLAIM_VERIFY_CONCURRENCY_ENV
        self.assertEqual(correctness_policy.claim_verify_concurrency({}), 2)
        self.assertEqual(
            correctness_policy.claim_verify_concurrency({env: "1"}),
            1,
        )
        self.assertEqual(
            correctness_policy.claim_verify_concurrency({env: "2"}),
            2,
        )
        for value in ("0", "3", "many"):
            with self.assertRaisesRegex(ValueError, "must be an integer"):
                correctness_policy.claim_verify_concurrency({env: value})

    def test_agent_can_only_submit_candidate_claims(self):
        self.assertEqual(
            correctness_policy.require_agent_submittable_status(" candidate "),
            "candidate",
        )
        for status in correctness_policy.ORCHESTRATOR_ONLY_CLAIM_STATUSES:
            with self.assertRaisesRegex(ValueError, "must be candidate"):
                correctness_policy.require_agent_submittable_status(status)

    def test_same_version_cannot_be_resurrected_or_rewritten(self):
        self.assertTrue(correctness_policy.can_transition_claim_status("candidate", "verified"))
        self.assertTrue(correctness_policy.can_transition_claim_status("verified", "disputed"))
        self.assertFalse(correctness_policy.can_transition_claim_status("disputed", "candidate"))
        self.assertFalse(correctness_policy.can_transition_claim_status("superseded", "verified"))
        with self.assertRaisesRegex(ValueError, "illegal claim status transition"):
            correctness_policy.require_claim_status_transition("verified", "candidate")

    def test_policy_snapshot_is_explicit_and_json_serializable(self):
        snapshot = correctness_policy.policy_snapshot()
        self.assertEqual(snapshot["policy_version"], correctness_policy.CORRECTNESS_POLICY_VERSION)
        self.assertFalse(snapshot["shadow_default"])
        self.assertEqual(
            set(snapshot["certificate_requirements"]),
            set(correctness_policy.CLAIM_KINDS),
        )


if __name__ == "__main__":
    unittest.main()
