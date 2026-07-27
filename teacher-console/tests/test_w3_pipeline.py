import sys
import unittest
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1]
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import w3_pipeline


BLUEPRINT = {
    "status": "completed",
    "question_targets": [{"id": "q1", "prompt": "第一次返回", "answer_type": "value"}],
    "physical_stages": [{"id": "p1"}],
    "verification_obligations": [
        {"id": "v1", "target_id": "q1", "check": "第一次事件", "risk": "critical"}
    ],
}
SOLUTION = {
    "status": "completed",
    "targets": [{
        "id": "q1",
        "prompt": "第一次返回",
        "final_answer": "t",
        "supporting_relations": ["t=T/2"],
    }],
    "stage_results": [{"stage_id": "p1", "result": "由周期关系得到目标结论。"}],
    "blueprint_audit": {"status": "followed"},
}


class W3PipelineTest(unittest.TestCase):
    def test_simple_problem_stays_on_w2(self):
        called = []
        result = w3_pipeline.run_shadow(
            "物体做匀速直线运动，求速度。",
            stage_runner=lambda name, context: called.append(name),
            evidence_builder=lambda blueprint: {},
        )
        self.assertEqual(result["screen"]["decision"], "w2")
        self.assertEqual(called, [])

    def test_critical_target_triggers_blind_solver_and_adjudication(self):
        calls = []

        def runner(name, context):
            calls.append((name, context))
            if name == "decompose":
                return BLUEPRINT
            if name == "solver-a":
                return SOLUTION
            if name == "verifier":
                return {
                    "status": "completed",
                    "target_audits": [{
                        "target_id": "q1",
                        "verdict": "conflict",
                        "recomputed_result": "2t",
                        "decisive_checks": ["检查首次事件"],
                        "issues": ["事件序号不一致"],
                    }],
                }
            if name == "solver-b":
                return {
                    **SOLUTION,
                    "targets": [{**SOLUTION["targets"][0], "final_answer": "2t"}],
                }
            if name == "adjudicator":
                return {"status": "completed", "target_decisions": []}
            raise AssertionError(name)

        result = w3_pipeline.run_shadow(
            "粒子先进入磁场，随后第一次返回边界，求时间。",
            stage_runner=runner,
            evidence_builder=lambda blueprint: {
                "status": "ready", "evidence_set": {"status": "selected"}
            },
        )
        self.assertEqual(
            [name for name, _ in calls],
            ["decompose", "solver-a", "verifier", "solver-b", "adjudicator"],
        )
        verifier_context = next(context for name, context in calls if name == "verifier")
        self.assertNotIn("historical_answer", verifier_context)
        self.assertEqual(result["metrics"]["teacher_focus_count"], 1)
        snapshot = w3_pipeline.teacher_review_snapshot(result)
        self.assertEqual(snapshot[0]["target_id"], "q1")
        labels = [item["label"] for item in snapshot[0]["audit"]["sections"]]
        self.assertIn("主候选结论", labels)
        self.assertIn("独立复算结论", labels)
        self.assertIn("交叉候选结论", labels)
        self.assertNotIn("solver", str(snapshot).lower())

    def test_acceptance_requires_independent_holdout(self):
        cases = [{
            "review_status": "approved",
            "evaluation_split": "holdout",
            "target_count": 3,
            "correct_target_count": 3,
            "w2_correct_target_count": 2,
            "agent_call_count": 4,
            "teacher_focus_count": 1,
        }] * 5
        metrics = w3_pipeline.acceptance_metrics(cases)
        self.assertTrue(metrics["gates"]["production_eligible"])

    def test_accuracy_regression_blocks_production(self):
        cases = [{
            "review_status": "approved",
            "evaluation_split": "holdout",
            "target_count": 3,
            "correct_target_count": 2,
            "w2_correct_target_count": 3,
            "agent_call_count": 4,
            "teacher_focus_count": 1,
        }] * 5
        metrics = w3_pipeline.acceptance_metrics(cases)
        self.assertFalse(metrics["gates"]["accuracy_non_regression"])

    def test_post_shadow_reference_revision_requires_fresh_holdout(self):
        cases = [{
            "review_status": "approved",
            "evaluation_split": "holdout",
            "target_count": 3,
            "correct_target_count": 3,
            "w2_correct_target_count": 3,
            "validated_supplement_target_count": 1,
            "reference_revised_after_shadow": True,
            "agent_call_count": 4,
            "teacher_focus_count": 1,
        }] * 5
        metrics = w3_pipeline.acceptance_metrics(cases)
        self.assertTrue(metrics["gates"]["accuracy_non_regression"])
        self.assertFalse(metrics["gates"]["independent_holdout_intact"])
        self.assertTrue(metrics["gates"]["fresh_holdout_required"])
        self.assertFalse(metrics["gates"]["production_eligible"])

    def test_replay_cases_are_reported_but_never_count_as_holdout(self):
        cases = [{
            "review_status": "approved",
            "evaluation_split": "replay",
            "target_count": 3,
            "correct_target_count": 3,
            "w2_correct_target_count": 3,
            "agent_call_count": 4,
            "teacher_focus_count": 1,
        }] * 5
        metrics = w3_pipeline.acceptance_metrics(cases)
        self.assertEqual(metrics["replay"]["target_count"], 15)
        self.assertEqual(metrics["replay"]["target_accuracy"], 1.0)
        self.assertFalse(metrics["gates"]["holdout_ready"])
        self.assertTrue(metrics["gates"]["fresh_holdout_required"])
        self.assertFalse(metrics["gates"]["production_eligible"])

    def test_second_solver_receives_only_challenge_targets(self):
        blueprint = {
            "question_targets": [{"id": "q1"}, {"id": "q2"}],
            "physical_stages": [{"id": "p1"}],
            "reasoning_steps": [
                {"id": "r1", "target_ids": ["q1"]},
                {"id": "r2", "target_ids": ["q2"]},
            ],
            "retrieval_needs": [
                {"id": "n1", "target_ids": ["q1"]},
                {"id": "n2", "target_ids": ["q2"]},
            ],
            "verification_obligations": [
                {"id": "v1", "target_id": "q1"},
                {"id": "v2", "target_id": "q2"},
            ],
        }
        projected = w3_pipeline.project_blueprint(blueprint, {"q2"})
        self.assertEqual([item["id"] for item in projected["question_targets"]], ["q2"])
        self.assertEqual([item["id"] for item in projected["verification_obligations"]], ["v2"])

    def test_renderer_outputs_one_answer_with_required_student_sections(self):
        blueprint = {
            "reasoning_steps": [{"operation": "列半径关系"}],
            "verification_obligations": [
                {"id": "v1", "target_id": "q1", "risk": "high", "check": "核对首次事件"}
            ],
        }
        solver = {
            "status": "completed",
            "targets": [{"id": "q1", "final_answer": "A"}],
            "stage_results": [{"stage_id": "p1", "result": "由 qvB=mv²/R 得 A。"}],
        }
        rendered = w3_pipeline.render_recommended_student_solution(
            blueprint, solver, None
        )
        for heading in ("答案速览", "一眼识别", "详细解答", "易错点", "30 秒自测"):
            self.assertIn(heading, rendered)
        self.assertIn("最短主线", rendered)

    def test_answer_signature_ignores_common_symbol_spelling(self):
        self.assertTrue(w3_pipeline.answers_equivalent("2v₀/(3π)", "2 v0 / (3\\pi)"))
        self.assertTrue(
            w3_pipeline.answers_equivalent(
                "t首次相遇=3πm/(qB)", "t首次相遇=3τ=3πm/(qB)"
            )
        )

    def test_optional_blind_solver_failure_keeps_primary_result_with_focus(self):
        def runner(name, _context):
            if name == "decompose":
                return BLUEPRINT
            if name == "solver-a":
                return SOLUTION
            if name == "verifier":
                return {
                    "status": "completed",
                    "target_audits": [{
                        "target_id": "q1",
                        "verdict": "pass",
                        "recomputed_result": "t",
                        "decisive_checks": ["独立复算"],
                        "issues": [],
                    }],
                }
            if name == "solver-b":
                raise ValueError("missing target")
            raise AssertionError(name)

        result = w3_pipeline.run_shadow(
            "粒子随后第一次返回边界，求时间。",
            stage_runner=runner,
            evidence_builder=lambda _blueprint: {
                "status": "ready", "evidence_set": {"selection_status": "selected"}
            },
        )
        self.assertEqual(result["solver_a"]["status"], "completed")
        self.assertEqual(result["solver_b"]["status"], "failed")
        self.assertEqual(result["metrics"]["teacher_focus_count"], 1)


if __name__ == "__main__":
    unittest.main()
