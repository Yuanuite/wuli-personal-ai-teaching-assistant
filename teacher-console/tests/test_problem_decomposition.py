import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "teacher-console"))

import problem_decomposition  # noqa: E402


def valid_blueprint():
    return {
        "status": "completed",
        "message": "已完成双层拆解",
        "question_targets": [{"id": "Q1", "prompt": "求首次相遇时间", "answer_type": "time"}],
        "physical_stages": [
            {
                "id": "P1",
                "label": "上半平面磁场运动",
                "entry_conditions": ["从原点进入"],
                "exit_conditions": ["回到 x 轴"],
                "state_carried": ["速度大小不变"],
            },
            {
                "id": "P2",
                "label": "下半平面磁场运动",
                "entry_conditions": ["穿过 x 轴"],
                "exit_conditions": ["再次回到 x 轴"],
                "state_carried": ["横坐标", "速度方向"],
            },
        ],
        "stage_transitions": [{"from_stage": "P1", "to_stage": "P2", "event": "粒子穿过 x 轴"}],
        "reasoning_steps": [
            {
                "id": "S1",
                "operation": "列出两粒子过界事件",
                "cognitive_operation": "apply",
                "depends_on": [],
                "target_ids": ["Q1"],
                "decisive_relations": ["t=φm/(qB)"],
                "knowledge_units": [{"id": "circular_motion", "relation_indexes": [0]}],
            },
            {
                "id": "S2",
                "operation": "比较公共位置的到达时刻",
                "cognitive_operation": "audit",
                "depends_on": ["S1"],
                "target_ids": ["Q1"],
                "decisive_relations": ["不存在更早的公共位置事件"],
                "knowledge_units": [{"id": "geometry_constraint", "relation_indexes": [0]}],
            },
        ],
        "stage_step_links": [
            {"stage_id": "P1", "step_id": "S1"},
            {"stage_id": "P2", "step_id": "S1"},
        ],
        "retrieval_needs": [
            {
                "id": "R1",
                "purpose": "核对磁场半圆时间",
                "query": "带电粒子 磁场 半圆 时间 相遇",
                "priority": 5,
                "target_ids": ["Q1"],
                "stage_ids": ["P1", "P2"],
            }
        ],
        "verification_obligations": [
            {
                "id": "V1",
                "target_id": "Q1",
                "check": "排除更早的非轴上相遇",
                "risk": "critical",
            }
        ],
        "type_distance": {
            "mode": "non_obvious_bridge",
            "archetype": "带电粒子周期运动",
            "recognition_barrier": "需要把相遇条件改写为事件时序",
            "novel_bridge": "用公共位置事件筛选首次相遇",
        },
    }


class ProblemDecompositionTest(unittest.TestCase):
    def test_production_and_shadow_contracts_keep_scoring_fields_separate(self):
        production = problem_decomposition.output_contract()["schema"]
        shadow = problem_decomposition.assessment_output_contract()["schema"]
        production_step = production["properties"]["reasoning_steps"]["items"]
        shadow_step = shadow["properties"]["reasoning_steps"]["items"]
        self.assertNotIn("cognitive_operation", production_step["properties"])
        self.assertNotIn("knowledge_units", production_step["properties"])
        self.assertNotIn("type_distance", production["properties"])
        self.assertIn("cognitive_operation", shadow_step["properties"])
        self.assertIn("knowledge_units", shadow_step["properties"])
        self.assertIn("type_distance", shadow["properties"])

    def test_complexity_screen_is_deterministic_and_selective(self):
        simple = problem_decomposition.complexity_screen("质量为 m 的物体速度为 v，求动能。")
        complex_case = problem_decomposition.complexity_screen(
            "甲、乙两粒子先后进入两个磁场区域，求第一次相遇时间和所有可能位置。"
        )
        self.assertEqual(simple["decision"], "w2")
        self.assertEqual(complex_case["decision"], "decompose")
        self.assertGreaterEqual(complex_case["score"], 2)

    def test_default_obligation_suggests_all_solutions_when_not_limited(self):
        blueprint = {
            "question_targets": [{"id": "Q1", "prompt": "求粒子进入区域的时刻", "answer_type": "time"}],
            "verification_obligations": [{"id": "V1", "target_id": "Q1", "check": "复算进入时刻", "risk": "medium"}],
        }
        suggestions = problem_decomposition.infer_default_obligation_suggestions("粒子可多次穿过区域边界。", blueprint)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["source"], "default-convention")
        self.assertEqual(suggestions[0]["rule_id"], "default.solve.all-physical-solutions.v1")
        self.assertEqual(suggestions[0]["target_id"], "Q1")

    def test_default_obligation_is_suppressed_by_first_event_wording(self):
        suggestions = problem_decomposition.infer_default_obligation_suggestions(
            "粒子进入磁场后第一次返回边界，求首次返回时间。",
            valid_blueprint(),
        )
        self.assertEqual(suggestions, [])

    def test_inverse_modeling_and_three_targets_trigger_decomposition(self):
        result = problem_decomposition.complexity_screen(
            "由热功率随位置变化反推轨道方程。（1）求方程；（2）求安培力；（3）求功。"
        )
        self.assertEqual(result["decision"], "decompose")
        self.assertIn("inverse-modeling", {item["id"] for item in result["risk_signals"]})

    def test_blueprint_normalization_accepts_two_layer_graph(self):
        result = problem_decomposition.normalize_payload(valid_blueprint())
        self.assertEqual(result["question_targets"][0]["id"], "Q1")
        self.assertEqual([item["id"] for item in result["reasoning_steps"]], ["S1", "S2"])
        self.assertEqual(result["retrieval_needs"][0]["id"], "R1")
        self.assertNotIn("cognitive_operation", result["reasoning_steps"][0])
        self.assertNotIn("knowledge_units", result["reasoning_steps"][0])
        self.assertNotIn("type_distance", result)

    def test_production_solve_contract_does_not_reject_scoring_annotation_gaps(self):
        payload = valid_blueprint()
        payload["reasoning_steps"][1]["decisive_relations"] = []
        payload["reasoning_steps"][1].pop("cognitive_operation")
        payload["reasoning_steps"][1].pop("knowledge_units")
        result = problem_decomposition.normalize_payload(payload)
        self.assertEqual(result["reasoning_steps"][1]["decisive_relations"], [])

    def test_shadow_annotation_must_bind_an_existing_decisive_relation(self):
        payload = valid_blueprint()
        payload["reasoning_steps"][0]["knowledge_units"][0]["relation_indexes"] = [1]
        with self.assertRaisesRegex(ValueError, "valid decisive relation indexes"):
            problem_decomposition.split_assessment_payload(payload)

    def test_shadow_annotations_are_stripped_before_solving(self):
        payload = valid_blueprint()
        solve_blueprint, annotations = problem_decomposition.split_assessment_payload(payload)
        self.assertNotIn("type_distance", solve_blueprint)
        self.assertNotIn(
            "cognitive_operation",
            solve_blueprint["reasoning_steps"][0],
        )
        self.assertNotIn("knowledge_units", solve_blueprint["reasoning_steps"][0])
        self.assertEqual(
            annotations["reasoning_steps"][0]["cognitive_operation"],
            "apply",
        )
        self.assertEqual(
            annotations["type_distance"]["mode"],
            "non_obvious_bridge",
        )

    def test_blueprint_rejects_cycles_and_uncovered_targets(self):
        payload = valid_blueprint()
        payload["reasoning_steps"][0]["depends_on"] = ["S2"]
        with self.assertRaisesRegex(ValueError, "acyclic"):
            problem_decomposition.normalize_payload(payload)
        payload = valid_blueprint()
        payload["question_targets"].append({"id": "Q2", "prompt": "求位置", "answer_type": "position"})
        with self.assertRaisesRegex(ValueError, "covered"):
            problem_decomposition.normalize_payload(payload)


if __name__ == "__main__":
    unittest.main()
