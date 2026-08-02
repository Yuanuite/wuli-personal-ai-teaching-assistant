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
        "stage_step_links": [{"stage_id": "p1", "step_id": "r1"}],
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
        "stage_interfaces": [{
            "stage_id": "p1",
            "coordinate_frame": "lab",
            "time_origin": "entry",
            "directions": {"radial": "toward center"},
            "entry_state": {"speed": "v"},
            "exit_state": {"speed": "v"},
            "required_entry_keys": ["speed"],
            "carried_state_keys": ["speed"],
        }],
        "stage_transitions": [],
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

    def test_multifluid_column_accepts_density_ratio_with_full_interval(self):
        case_blueprint = blueprint()
        case_blueprint["verification_obligations"].append({
            "id": "MFC_q1_domain",
            "target_id": "q1",
        })
        payload = solution()
        payload["targets"][0].update({
            "final_answer": (
                "F_x = 1/2*w*g*h^2*rho_0*(rho_0-rho_oil)/rho_oil，方向+x"
            ),
            "supporting_relations": [
                "H_oil=rho_0*h/rho_oil",
                "F_oil=1/2*rho_oil*g*w*H_oil^2，覆盖完整油侧润湿区间",
            ],
            "covered_obligation_ids": ["v1", "MFC_q1_domain"],
        })
        payload["blueprint_audit"]["covered_obligation_ids"] = [
            "v1",
            "MFC_q1_domain",
        ]
        result = solution_reasoning.normalize_solution(payload, case_blueprint)
        self.assertIn("/rho_oil", result["targets"][0]["final_answer"])

    def test_multifluid_column_rejects_simple_density_difference(self):
        case_blueprint = blueprint()
        case_blueprint["verification_obligations"].append({
            "id": "MFC_q1_domain",
            "target_id": "q1",
        })
        payload = solution()
        payload["targets"][0].update({
            "final_answer": "F_x = 1/2*w*g*h^2*(rho_0-rho_oil)",
            "supporting_relations": [
                "H_oil=rho_0*h/rho_oil",
                "F_oil=1/2*rho_oil*g*w*H_oil^2，覆盖完整油侧润湿区间",
            ],
            "covered_obligation_ids": ["v1", "MFC_q1_domain"],
        })
        payload["blueprint_audit"]["covered_obligation_ids"] = [
            "v1",
            "MFC_q1_domain",
        ]
        with self.assertRaisesRegex(ValueError, "density ratio"):
            solution_reasoning.normalize_solution(payload, case_blueprint)

    def test_reasoning_step_results_map_to_their_physical_stage(self):
        payload = solution()
        payload["stage_results"] = [
            {"stage_id": "r1", "result": "列弹力"},
            {"stage_id": "r1", "result": "列向心力"},
        ]
        result = solution_reasoning.normalize_solution(payload, blueprint())
        self.assertEqual(result["stage_results"][0]["stage_id"], "p1")
        self.assertIn("列弹力；列向心力", result["stage_results"][0]["result"])

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

    def test_adjudication_rejects_hidden_control_characters(self):
        payload = {
            "status": "completed",
            "message": "ok",
            "target_decisions": [{
                "target_id": "q1",
                "selected_result": "A",
                "decision": "recomputed",
                "decisive_relation": "qvB=\x12mv²/R",
                "reason": "可复算",
            }],
        }
        with self.assertRaisesRegex(ValueError, "unsupported control"):
            solution_reasoning.normalize_adjudication(payload, {"q1"})

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

    def test_projects_existing_solution_to_candidate_claim_ledger(self):
        result = solution_reasoning.project_claim_ledger(
            solution(),
            blueprint(),
            input_fingerprint="b" * 64,
            snapshot_version=3,
        )
        self.assertEqual(result["snapshot_version"], 3)
        self.assertTrue(result["claims"])
        self.assertTrue(all(item["status"] == "candidate" for item in result["claims"]))
        final_claim = next(
            item for item in result["claims"] if item["kind"] == "final"
        )
        self.assertEqual(final_claim["target_ids"], ["q1"])
        self.assertEqual(final_claim["obligation_ids"], ["v1"])
        relation_claim = next(
            item
            for item in result["claims"]
            if item["kind"] == "derived"
        )
        self.assertEqual(
            relation_claim["check_spec"]["type"], "semantic-required"
        )


if __name__ == "__main__":
    unittest.main()
