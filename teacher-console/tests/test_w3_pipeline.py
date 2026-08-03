import sys
import threading
import unittest
from pathlib import Path
from typing import Any

CONSOLE = Path(__file__).resolve().parents[1]
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

import w3_pipeline

BLUEPRINT = {
    "status": "completed",
    "question_targets": [{"id": "q1", "prompt": "第一次返回", "answer_type": "value"}],
    "physical_stages": [{"id": "p1"}],
    "verification_obligations": [{"id": "v1", "target_id": "q1", "check": "第一次事件", "risk": "critical"}],
}
SOLUTION: dict[str, Any] = {
    "status": "completed",
    "targets": [
        {
            "id": "q1",
            "prompt": "第一次返回",
            "final_answer": "t",
            "supporting_relations": ["t=T/2"],
        }
    ],
    "stage_results": [{"stage_id": "p1", "result": "由周期关系得到目标结论。"}],
    "blueprint_audit": {"status": "followed"},
}


class W3PipelineTest(unittest.TestCase):
    def test_multifluid_plate_gets_mandatory_full_wetted_interval_obligation(self):
        blueprint = {
            "question_targets": [{"id": "T1"}],
            "verification_obligations": [],
        }
        result = w3_pipeline.augment_mandatory_physics_obligations(
            "把密度较小的油倒入两板之间，板间水完全排开，油底到板下边缘，求合力。",
            blueprint,
        )
        obligation = result["verification_obligations"][0]
        self.assertTrue(obligation["id"].startswith("MFC_"))
        self.assertIn("完整润湿区间", obligation["check"])

    def test_source_domain_guard_recovers_explicit_missing_obligations(self):
        cases = [
            (
                "粒子第一次返回边界，求首次返回时间。",
                "求首次返回时间",
                "event_order",
                "排除所有更早",
            ),
            (
                "求所有可能的相遇位置。",
                "求所有可能位置",
                "branch_completeness",
                "全部物理可行分支",
            ),
            (
                "粒子穿过两个场区边界，求进入区域的时间范围。",
                "求进入区域的时间范围",
                "domain_boundary",
                "物理定义域",
            ),
            (
                "在地面参考系中求相对速度。",
                "求地面参考系中的相对速度",
                "reference_frame",
                "固定参考系",
            ),
        ]
        for problem, prompt, suffix, decisive_text in cases:
            with self.subTest(suffix=suffix):
                result = w3_pipeline.augment_mandatory_physics_obligations(
                    problem,
                    {
                        "question_targets": [{"id": "Q1", "prompt": prompt}],
                        "verification_obligations": [],
                    },
                )
                obligation = next(item for item in result["verification_obligations"] if item["id"].endswith(suffix))
                self.assertEqual(
                    obligation["id"],
                    f"{w3_pipeline.SOURCE_DOMAIN_OBLIGATION_PREFIX}Q1_{suffix}",
                )
                self.assertEqual(
                    obligation["source"],
                    "deterministic-source-domain-guard",
                )
                self.assertIn(decisive_text, obligation["check"])

    def test_source_domain_guard_does_not_duplicate_existing_coverage(self):
        blueprint = {
            "question_targets": [{"id": "Q1", "prompt": "求首次返回时间"}],
            "verification_obligations": [
                {
                    "id": "V1",
                    "target_id": "Q1",
                    "check": "排除更早事件并证明首次返回",
                    "risk": "critical",
                }
            ],
        }
        result = w3_pipeline.augment_mandatory_physics_obligations(
            "粒子第一次返回，求首次返回时间。",
            blueprint,
        )
        self.assertEqual(
            result["verification_obligations"],
            blueprint["verification_obligations"],
        )

    def test_claim_evidence_flag_off_preserves_existing_w3_summary(self):
        arguments: dict[str, Any] = {
            "problem": "物体做匀速直线运动，求速度。",
            "stage_runner": lambda name, context: None,
            "evidence_builder": lambda blueprint: {},
        }
        legacy = w3_pipeline.run_shadow(
            **arguments,
            claim_evidence_shadow_enabled=False,
        )
        default_off = w3_pipeline.run_shadow(
            **arguments,
            claim_evidence_shadow_enabled=False,
        )
        self.assertEqual(default_off, legacy)
        self.assertNotIn("claim_evidence_shadow", default_off)

        enabled = w3_pipeline.run_shadow(
            **arguments,
            claim_evidence_shadow_enabled=True,
        )
        self.assertEqual(enabled["claim_evidence_shadow"]["status"], "enabled-not-run")
        without_marker = dict(enabled)
        without_marker.pop("claim_evidence_shadow")
        self.assertEqual(without_marker, legacy)

    def test_enabled_claim_evidence_runs_isolated_claim_verifier(self):
        blueprint = {
            "status": "completed",
            "question_targets": [{"id": "q1", "prompt": "求结果", "answer_type": "value"}],
            "physical_stages": [{"id": "p1"}],
            "reasoning_steps": [],
            "stage_step_links": [],
            "verification_obligations": [
                {
                    "id": "v1",
                    "target_id": "q1",
                    "check": "核对结果",
                    "risk": "medium",
                }
            ],
        }
        solver: dict[str, Any] = {
            "status": "completed",
            "message": "ok",
            "targets": [
                {
                    "id": "q1",
                    "final_answer": "6",
                    "supporting_relations": ["2×3=6"],
                    "conditions": [],
                    "covered_obligation_ids": ["v1"],
                }
            ],
            "stage_results": [
                {
                    "stage_id": "p1",
                    "result": "单阶段复算完成。",
                }
            ],
            "stage_interfaces": [
                {
                    "stage_id": "p1",
                    "coordinate_frame": "ground",
                    "time_origin": "t=0",
                    "directions": {"x": "positive along motion"},
                    "entry_state": {"speed": "initial speed"},
                    "exit_state": {"speed": "speed at target event"},
                    "required_entry_keys": ["speed"],
                    "carried_state_keys": [],
                }
            ],
            "stage_transitions": [],
            "option_verdicts": [],
            "blueprint_audit": {
                "status": "followed",
                "covered_target_ids": ["q1"],
                "covered_obligation_ids": ["v1"],
                "revisions": [],
            },
            "_runtime_identity": {
                "model_id": "solver-model",
                "provider": "fake",
                "context_isolated": False,
            },
        }
        calls = []

        def runner(name, context):
            calls.append(name)
            if name == "decompose":
                return blueprint
            if name == "solver-a":
                obligation_ids = [item["id"] for item in context["blueprint"]["verification_obligations"]]
                return {
                    **solver,
                    "targets": [
                        {
                            **solver["targets"][0],
                            "covered_obligation_ids": obligation_ids,
                        }
                    ],
                    "blueprint_audit": {
                        **solver["blueprint_audit"],
                        "covered_obligation_ids": obligation_ids,
                    },
                }
            if name == "verifier":
                return {
                    "status": "completed",
                    "target_audits": [
                        {
                            "target_id": "q1",
                            "verdict": "pass",
                            "recomputed_result": "6",
                            "decisive_checks": ["2×3=6"],
                            "issues": [],
                        }
                    ],
                }
            if name == "claim-verifier":
                audits = []
                for request in context["verification_view"]["requests"]:
                    item = request["claim"]
                    audits.append({
                        "claim_id": item["id"],
                        "claim_version": item["version"],
                        "verdict": "pass",
                        "normalized_result": "independently checked",
                        "decisive_checks": ["recomputed from approved problem"],
                        "issues": [],
                    })
                return {
                    "status": "completed",
                    "message": "checked",
                    "claim_audits": audits,
                    "interface_audit": {
                        "verdict": "pass",
                        "decisive_checks": ["single-stage interface is complete"],
                        "issues": [],
                    },
                    "_runtime_identity": {
                        "model_id": "verifier-model",
                        "provider": "fake",
                        "context_isolated": True,
                    },
                }
            raise AssertionError(name)

        result = w3_pipeline.run_shadow(
            "一个复杂物理过程包含三个阶段并要求求出所有可能的结果。",
            stage_runner=runner,
            evidence_builder=lambda _blueprint: {
                "status": "ready",
                "evidence_set": {"status": "selected"},
            },
            claim_evidence_shadow_enabled=True,
        )
        self.assertIn("claim-verifier", calls)
        evidence = result["claim_evidence_shadow"]
        self.assertEqual(evidence["status"], "completed")
        self.assertTrue(evidence["ledger"]["claims"])
        self.assertTrue(evidence["certificates"])
        self.assertEqual(evidence["aggregation"]["status"], "VERIFIED")
        self.assertEqual(evidence["stage_interface_report"]["status"], "pass")
        self.assertEqual(result["w3r_shadow"]["status"], "completed")
        self.assertTrue(result["w3r_shadow"]["production_candidate_unchanged"])
        self.assertFalse(result["w3r_shadow"]["solver_fallback_allowed"])
        teacher_view = w3_pipeline.claim_evidence_teacher_snapshot(result)
        self.assertEqual(teacher_view["status"], "completed")
        self.assertEqual(teacher_view["aggregation_status"], "VERIFIED")
        self.assertEqual(teacher_view["final_answers"][0]["statement"], "6")
        self.assertEqual(len(teacher_view["claims"]), evidence["metrics"]["claim_count"])
        self.assertTrue(teacher_view["certificates"])
        self.assertFalse(any(item["type"] == "stage-interface" for item in teacher_view["unresolved_obligations"]))
        encoded = str(teacher_view)
        self.assertNotIn("_runtime_identity", encoded)
        self.assertNotIn("input_fingerprint", encoded)
        self.assertNotIn("semantic_audit", encoded)

    def test_claim_batches_can_run_two_at_a_time_without_reordering_results(self):
        blueprint = {
            "status": "completed",
            "question_targets": [{"id": "q1", "prompt": "求结果", "answer_type": "value"}],
            "physical_stages": [],
            "reasoning_steps": [],
            "stage_step_links": [],
            "verification_obligations": [
                {
                    "id": "v1",
                    "target_id": "q1",
                    "check": "核对结果",
                    "risk": "critical",
                }
            ],
        }
        solver: dict[str, Any] = {
            "status": "completed",
            "message": "ok",
            "targets": [
                {
                    "id": "q1",
                    "final_answer": "45",
                    "supporting_relations": [f"r{index}={index}" for index in range(1, 10)],
                    "conditions": [],
                    "covered_obligation_ids": ["v1"],
                }
            ],
            "stage_results": [],
            "stage_interfaces": [],
            "stage_transitions": [],
            "option_verdicts": [],
            "blueprint_audit": {
                "status": "followed",
                "covered_target_ids": ["q1"],
                "covered_obligation_ids": ["v1"],
                "revisions": [],
            },
            "_runtime_identity": {
                "model_id": "solver-model",
                "provider": "fake",
            },
        }
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        max_active = 0

        def runner(name, context):
            nonlocal active, max_active
            self.assertEqual(name, "claim-verifier")
            with lock:
                active += 1
                max_active = max(max_active, active)
            barrier.wait(timeout=2)
            audits = [
                {
                    "claim_id": request["claim"]["id"],
                    "claim_version": request["claim"]["version"],
                    "verdict": "pass",
                    "normalized_result": "checked",
                    "decisive_checks": ["independent recomputation"],
                    "issues": [],
                }
                for request in context["verification_view"]["requests"]
            ]
            with lock:
                active -= 1
            return {
                "status": "completed",
                "message": f"batch-{context['batch_index']}",
                "claim_audits": audits,
                "interface_audit": {
                    "verdict": "pass",
                    "decisive_checks": ["no stage interfaces required"],
                    "issues": [],
                },
                "_runtime_identity": {
                    "model_id": "verifier-model",
                    "provider": "fake",
                    "context_isolated": True,
                },
            }

        evidence = w3_pipeline.run_claim_evidence_shadow(
            "复杂题，求最终结果。",
            blueprint,
            solver,
            stage_runner=runner,
            risk_decisions=[{"target_id": "q1", "risk": 1.0}],
            claim_verifier_concurrency=2,
        )

        audit = evidence["semantic_audit"]
        self.assertEqual(max_active, 2)
        self.assertEqual(audit["batch_count"], 2)
        self.assertEqual(audit["batch_concurrency"], 2)
        self.assertEqual(
            audit["optimization_assessment"]["semantic_request_count"],
            10,
        )
        self.assertEqual(
            audit["optimization_assessment"]["safe_skippable_claim_count"],
            0,
        )
        self.assertEqual(audit["message"], "batch-0；batch-1")
        self.assertEqual(evidence["aggregation"]["status"], "VERIFIED")

    def test_default_obligation_suggestions_are_shadow_only(self):
        blueprint = {
            "status": "completed",
            "question_targets": [{"id": "q1", "prompt": "求粒子进入区域的时刻", "answer_type": "time"}],
            "physical_stages": [{"id": "p1"}],
            "reasoning_steps": [],
            "stage_step_links": [],
            "verification_obligations": [
                {
                    "id": "v1",
                    "target_id": "q1",
                    "check": "复算进入时刻",
                    "risk": "medium",
                }
            ],
        }
        solver: dict[str, Any] = {
            "status": "completed",
            "message": "ok",
            "targets": [
                {
                    "id": "q1",
                    "final_answer": "t=T",
                    "supporting_relations": ["t=T"],
                    "conditions": [],
                    "covered_obligation_ids": ["v1"],
                }
            ],
            "stage_results": [],
            "option_verdicts": [],
            "blueprint_audit": {
                "status": "followed",
                "covered_target_ids": ["q1"],
                "covered_obligation_ids": ["v1"],
                "revisions": [],
            },
        }

        def runner(name, context):
            if name == "decompose":
                return blueprint
            if name == "solver-a":
                self.assertNotIn("default_obligation_suggestions", context["blueprint"])
                return solver
            raise AssertionError(name)

        result = w3_pipeline.run_shadow(
            "粒子在两个区域边界间往返，求粒子进入区域的时刻。",
            stage_runner=runner,
            evidence_builder=lambda _blueprint: {"status": "empty"},
            claim_evidence_shadow_enabled=False,
        )
        suggestions = result["default_obligation_suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["source"], "default-convention")
        self.assertEqual(suggestions[0]["target_id"], "q1")

    def test_claim_evidence_teacher_snapshot_fails_closed_for_legacy_report(self):
        snapshot = w3_pipeline.claim_evidence_teacher_snapshot({
            "claim_evidence_shadow": {
                "status": "failed",
                "message": "/private/path and raw provider details",
            }
        })
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["final_answers"], [])
        self.assertEqual(snapshot["unresolved_obligations"], [])
        self.assertNotIn("/private/path", str(snapshot))

    def test_simple_problem_stays_on_w2(self):
        called = []

        def _track_runner(name: str, context: Any) -> dict:
            called.append(name)
            return {}

        result = w3_pipeline.run_shadow(
            "物体做匀速直线运动，求速度。",
            stage_runner=_track_runner,
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
                    "target_audits": [
                        {
                            "target_id": "q1",
                            "verdict": "conflict",
                            "recomputed_result": "2t",
                            "decisive_checks": ["检查首次事件"],
                            "issues": ["事件序号不一致"],
                        }
                    ],
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
            evidence_builder=lambda blueprint: {"status": "ready", "evidence_set": {"status": "selected"}},
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
        cases = [
            {
                "review_status": "approved",
                "evaluation_split": "holdout",
                "target_count": 3,
                "correct_target_count": 3,
                "w2_correct_target_count": 2,
                "agent_call_count": 4,
                "teacher_focus_count": 1,
            }
        ] * 5
        metrics = w3_pipeline.acceptance_metrics(cases)
        self.assertTrue(metrics["gates"]["production_eligible"])

    def test_accuracy_regression_blocks_production(self):
        cases = [
            {
                "review_status": "approved",
                "evaluation_split": "holdout",
                "target_count": 3,
                "correct_target_count": 2,
                "w2_correct_target_count": 3,
                "agent_call_count": 4,
                "teacher_focus_count": 1,
            }
        ] * 5
        metrics = w3_pipeline.acceptance_metrics(cases)
        self.assertFalse(metrics["gates"]["accuracy_non_regression"])

    def test_post_shadow_reference_revision_requires_fresh_holdout(self):
        cases = [
            {
                "review_status": "approved",
                "evaluation_split": "holdout",
                "target_count": 3,
                "correct_target_count": 3,
                "w2_correct_target_count": 3,
                "validated_supplement_target_count": 1,
                "reference_revised_after_shadow": True,
                "agent_call_count": 4,
                "teacher_focus_count": 1,
            }
        ] * 5
        metrics = w3_pipeline.acceptance_metrics(cases)
        self.assertTrue(metrics["gates"]["accuracy_non_regression"])
        self.assertFalse(metrics["gates"]["independent_holdout_intact"])
        self.assertTrue(metrics["gates"]["fresh_holdout_required"])
        self.assertFalse(metrics["gates"]["production_eligible"])

    def test_replay_cases_are_reported_but_never_count_as_holdout(self):
        cases = [
            {
                "review_status": "approved",
                "evaluation_split": "replay",
                "target_count": 3,
                "correct_target_count": 3,
                "w2_correct_target_count": 3,
                "agent_call_count": 4,
                "teacher_focus_count": 1,
            }
        ] * 5
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
            "verification_obligations": [{"id": "v1", "target_id": "q1", "risk": "high", "check": "核对首次事件"}],
        }
        solver: dict[str, Any] = {
            "status": "completed",
            "targets": [{"id": "q1", "final_answer": "A"}],
            "stage_results": [{"stage_id": "p1", "result": "由 qvB=mv²/R 得 A。"}],
        }
        rendered = w3_pipeline.render_recommended_student_solution(blueprint, solver, None)
        for heading in ("答案速览", "一眼识别", "详细解答", "易错点", "30 秒自测"):
            self.assertIn(heading, rendered)
        self.assertIn("最短主线", rendered)

    def test_renderer_uses_high_school_release_constraint_in_student_layer(self):
        blueprint = {
            "reasoning_steps": [
                {"operation": "二次微分绳长约束并消去速度平方项"},
                {"operation": "建立牛顿第二定律"},
            ],
            "verification_obligations": [
                {
                    "id": "v1",
                    "target_id": "q1",
                    "risk": "critical",
                    "check": (
                        "确认二次微分中的速度平方项只因释放瞬间初速度为零而消失，"
                        "不能把所得加速度关系误当作全过程恒成立的常比例关系。"
                    ),
                },
                {
                    "id": "v2",
                    "target_id": "q1",
                    "risk": "high",
                    "check": "检查正方向约定与绳长微分符号一致。",
                },
            ],
        }
        solver: dict[str, Any] = {
            "status": "completed",
            "targets": [{"id": "q1", "final_answer": "a_R=a_L cosθ"}],
            "stage_results": [
                {
                    "stage_id": "p1",
                    "result": "由释放瞬间的几何关系得到 a_R=a_L cosθ。",
                }
            ],
        }
        rendered = w3_pipeline.render_recommended_student_solution(blueprint, solver, None)
        self.assertIn("s=at²/2", rendered)
        self.assertNotIn("微分", rendered)

    def test_renderer_uses_pressure_graph_area_instead_of_integration(self):
        blueprint = {
            "reasoning_steps": [
                {
                    "operation": "分别积分得到水侧和油侧对右板的水平压力合力",
                }
            ],
            "verification_obligations": [
                {
                    "id": "v1",
                    "target_id": "q1",
                    "risk": "high",
                    "check": "核对大气压在两侧压力差或表压积分中正确抵消。",
                }
            ],
        }
        solver = {
            "status": "completed",
            "targets": [{"id": "q1", "final_answer": "F 向右"}],
            "stage_results": [
                {
                    "stage_id": "p1",
                    "result": "线性压强图面积给出合力。",
                }
            ],
        }
        rendered = w3_pipeline.render_recommended_student_solution(blueprint, solver, None)
        self.assertIn("三角形压强-深度图", rendered)
        self.assertNotIn("积分", rendered)

    def test_answer_signature_ignores_common_symbol_spelling(self):
        self.assertTrue(w3_pipeline.answers_equivalent("2v₀/(3π)", "2 v0 / (3\\pi)"))
        self.assertTrue(w3_pipeline.answers_equivalent("t首次相遇=3πm/(qB)", "t首次相遇=3τ=3πm/(qB)"))

    def test_optional_blind_solver_failure_keeps_primary_result_with_focus(self):
        def runner(name, _context):
            if name == "decompose":
                return BLUEPRINT
            if name == "solver-a":
                return SOLUTION
            if name == "verifier":
                return {
                    "status": "completed",
                    "target_audits": [
                        {
                            "target_id": "q1",
                            "verdict": "pass",
                            "recomputed_result": "t",
                            "decisive_checks": ["独立复算"],
                            "issues": [],
                        }
                    ],
                }
            if name == "solver-b":
                raise ValueError("missing target")
            raise AssertionError(name)

        result = w3_pipeline.run_shadow(
            "粒子随后第一次返回边界，求时间。",
            stage_runner=runner,
            evidence_builder=lambda _blueprint: {"status": "ready", "evidence_set": {"selection_status": "selected"}},
        )
        self.assertEqual(result["solver_a"]["status"], "completed")
        self.assertEqual(result["solver_b"]["status"], "failed")
        self.assertEqual(result["metrics"]["teacher_focus_count"], 1)


if __name__ == "__main__":
    unittest.main()
