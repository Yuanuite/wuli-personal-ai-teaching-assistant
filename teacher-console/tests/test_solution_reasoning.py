import sys
import unittest
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1]
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import solution_reasoning


def blueprint():
    return {
        "question_targets": [{"id": "q1"}],
        "physical_stages": [{"id": "p1"}],
        "verification_obligations": [{"id": "v1", "target_id": "q1"}],
    }


def solution():
    return {
        "status": "completed",
        "message": "ok",
        "targets": [{
            "id": "q1",
            "final_answer": "A",
            "supporting_relations": ["qvB=mv²/R"],
            "conditions": ["垂直入射"],
            "covered_obligation_ids": ["v1"],
        }],
        "stage_results": [{"stage_id": "p1", "result": "圆周运动"}],
        "option_verdicts": [{"option": "A", "verdict": "correct", "reason": "满足半径关系"}],
        "blueprint_audit": {
            "status": "followed",
            "covered_target_ids": ["q1"],
            "covered_obligation_ids": ["v1"],
            "revisions": [],
        },
    }


class SolutionReasoningTest(unittest.TestCase):
    def test_normalizes_complete_solution(self):
        result = solution_reasoning.normalize_solution(solution(), blueprint())
        self.assertEqual(result["targets"][0]["final_answer"], "A")

    def test_rejects_missing_obligation(self):
        payload = solution()
        payload["targets"][0]["covered_obligation_ids"] = []
        with self.assertRaisesRegex(ValueError, "obligation"):
            solution_reasoning.normalize_solution(payload, blueprint())

    def test_adjudication_requires_every_conflict(self):
        payload = {
            "status": "completed",
            "message": "ok",
            "target_decisions": [{
                "target_id": "q1",
                "selected_result": "A",
                "decision": "recomputed",
                "decisive_relation": "qvB=mv²/R",
                "reason": "可复算",
            }],
        }
        result = solution_reasoning.normalize_adjudication(payload, {"q1"})
        self.assertEqual(result["target_decisions"][0]["decision"], "recomputed")

    def test_verified_equivalence_can_safely_fill_empty_adjudication(self):
        result = solution_reasoning.normalize_adjudication_with_verified_equivalence(
            {"status": "completed", "message": "两式等价", "target_decisions": []},
            {"q1"},
            solver_a={"targets": [{"id": "q1", "final_answer": "2v₀/(3π)"}]},
            verifier={"target_audits": [{
                "target_id": "q1",
                "verdict": "pass",
                "decisive_checks": ["独立复算相同"],
            }]},
        )
        self.assertEqual(result["target_decisions"][0]["selected_result"], "2v₀/(3π)")


if __name__ == "__main__":
    unittest.main()
