import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import solution_verification  # noqa: E402


FINGERPRINT = "d" * 64


def claim(claim_id="C1", *, depends_on=None):
    return {
        "id": claim_id,
        "version": 1,
        "kind": "model",
        "statement": "粒子在该阶段做匀速圆周运动。",
        "target_ids": ["Q1"],
        "stage_ids": ["P1"],
        "depends_on": depends_on or [],
        "conditions": ["仅受洛伦兹力"],
        "obligation_ids": ["V1"],
        "check_spec": {"type": "semantic-required"},
        "status": "candidate",
        "source": {
            "task_id": f"build-{claim_id}",
            "input_fingerprint": FINGERPRINT,
            "policy_version": "claim-ledger-v1",
        },
    }


class SolutionVerificationTest(unittest.TestCase):
    def test_verifier_evidence_view_removes_historical_answer_prose(self):
        view = solution_verification.verification_evidence_view({
            "status": "ready",
            "references": [{
                "reference": "similar-1",
                "title": "历史题",
                "methods": ["守恒"],
                "matched_evidence": [{"snippet": "历史答案正文"}],
                "secondary_conclusions": ["有条件结论"],
                "retrieval_target_ids": ["q1"],
            }],
        })
        serialized = str(view)
        self.assertNotIn("历史题", serialized)
        self.assertNotIn("历史答案正文", serialized)
        self.assertIn("有条件结论", serialized)

    def test_high_risk_uniqueness_target_has_positive_expected_gain(self):
        risk = solution_verification.target_risk(
            {"id": "Q3", "prompt": "求唯一释放时刻", "final_answer": "t=t0/2"},
            [{"target_id": "Q3", "check": "排除其他区间", "risk": "critical"}],
            evidence_status="selected",
            blueprint_status="followed",
        )
        decision = solution_verification.should_verify(
            risk,
            {
                "catch_probability": 0.8,
                "error_cost": 1.0,
                "verification_cost": 0.1,
                "false_conflict_probability": 0.05,
                "review_cost": 0.5,
            },
        )
        self.assertEqual(decision["decision"], "verify")

    def test_audit_requires_decisive_check_for_pass(self):
        with self.assertRaisesRegex(ValueError, "decisive"):
            solution_verification.normalize_audit(
                {
                    "status": "completed",
                    "message": "checked",
                    "target_audits": [
                        {
                            "target_id": "Q1",
                            "verdict": "pass",
                            "recomputed_result": "v",
                            "decisive_checks": [],
                            "issues": [],
                        }
                    ],
                },
                {"Q1"},
            )

    def test_teacher_focus_hides_internal_roles_and_caps_at_two(self):
        audits = [
            {
                "target_id": f"Q{index}",
                "verdict": "conflict" if index == 1 else "insufficient",
                "decisive_checks": [f"检查条件 {index}"],
                "issues": [f"结论 {index} 需要核对"],
            }
            for index in range(1, 4)
        ]
        focus = solution_verification.teacher_review_focus(audits, [])
        self.assertEqual(len(focus), 2)
        self.assertNotIn("unresolved", str(focus))
        self.assertNotIn("Solver", str(focus))

    def test_claim_verifier_receives_only_minimal_dependency_view(self):
        upstream = claim("C0")
        current = claim("C1", depends_on=["C0"])
        view = solution_verification.claim_verification_view(
            [{"claim": current, "dependencies": [upstream]}],
            [{
                "id": "S1",
                "statement": "题干给出磁场均匀。",
                "conditions": ["B 恒定"],
                "historical_answer": "不应进入视图",
                "local_path": "/private/answer.md",
            }],
        )
        serialized = str(view)
        self.assertNotIn("build-C1", serialized)
        self.assertNotIn(FINGERPRINT, serialized)
        self.assertNotIn("historical_answer", serialized)
        self.assertNotIn("/private/answer.md", serialized)
        self.assertEqual(
            view["requests"][0]["dependencies"][0]["id"], "C0"
        )

    def test_claim_audit_requires_exact_coverage_and_decisive_pass(self):
        payload = {
            "status": "completed",
            "message": "checked",
            "interface_audit": None,
            "claim_audits": [{
                "claim_id": "C1",
                "claim_version": 1,
                "verdict": "pass",
                "normalized_result": "模型适用",
                "decisive_checks": ["洛伦兹力始终与速度垂直"],
                "issues": [],
            }],
        }
        normalized = solution_verification.normalize_claim_audit(
            payload, {"C1": 1}
        )
        self.assertEqual(normalized["claim_audits"][0]["verdict"], "pass")

        missing = {**payload, "claim_audits": []}
        with self.assertRaisesRegex(ValueError, "cover every request"):
            solution_verification.normalize_claim_audit(
                missing, {"C1": 1}
            )
        no_check = {
            **payload,
            "claim_audits": [{
                **payload["claim_audits"][0],
                "decisive_checks": [],
            }],
        }
        with self.assertRaisesRegex(ValueError, "decisive check"):
            solution_verification.normalize_claim_audit(
                no_check, {"C1": 1}
            )

    def test_runtime_not_agent_attaches_semantic_certificate_identity(self):
        current = claim()
        view = solution_verification.claim_verification_view(
            [{"claim": current, "dependencies": []}],
            [{"id": "S1", "statement": "已批准题干事实"}],
        )
        fingerprint = view["requests"][0]["input_fingerprint"]
        audit = solution_verification.normalize_claim_audit(
            {
                "status": "completed",
                "message": "checked",
                "interface_audit": None,
                "claim_audits": [{
                    "claim_id": "C1",
                    "claim_version": 1,
                    "verdict": "pass",
                    "normalized_result": "模型适用",
                    "decisive_checks": ["独立重建受力模型"],
                    "issues": [],
                }],
            },
            {"C1": 1},
        )
        certificates = solution_verification.materialize_claim_certificates(
            audit,
            {("C1", 1): fingerprint},
            model_id="verifier-model",
            provider="test-adapter",
            context_isolated=True,
        )
        self.assertEqual(certificates[0]["input_fingerprint"], fingerprint)
        self.assertEqual(
            certificates[0]["verifier_identity"]["model_id"],
            "verifier-model",
        )
        with self.assertRaisesRegex(ValueError, "must be isolated"):
            solution_verification.materialize_claim_certificates(
                audit,
                {("C1", 1): fingerprint},
                model_id="same-context",
                provider="test-adapter",
                context_isolated=False,
            )


if __name__ == "__main__":
    unittest.main()
