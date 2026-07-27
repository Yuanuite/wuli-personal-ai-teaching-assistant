import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import solution_verification  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
