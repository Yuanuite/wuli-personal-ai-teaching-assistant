import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

from agent_gateway import AgentGateway  # noqa: E402
import evidence_agent  # noqa: E402
import evidence_contract  # noqa: E402


def need(*, minimum_authority="B", criticality="required"):
    return evidence_contract.normalize_retrieval_need(
        {
            "need_id": "N1",
            "purpose": "applicability_check",
            "question": "非弹性碰撞何时可以使用动量守恒？",
            "required_facets": ["系统外力冲量", "动量守恒条件"],
            "forbidden_conflicts": ["把机械能守恒当作必要条件"],
            "minimum_authority": minimum_authority,
            "criticality": criticality,
            "target_ids": ["Q1"],
            "stage_ids": ["P1"],
            "obligation_ids": ["V1"],
        }
    )


def unit(*, evidence_id="EU1", authority="B", source_kind="approved_solution"):
    return evidence_contract.normalize_evidence_unit(
        {
            "evidence_id": evidence_id,
            "unit_kind": "method_applicability",
            "source_kind": source_kind,
            "source_locator": {
                "path": "student-error-library/entries/old/teacher-solution.md",
                "section": "碰撞阶段",
                "start_line": 4,
                "end_line": 7,
            },
            "text": "碰撞时间极短且系统外力冲量可忽略时，系统动量守恒。",
            "physics_facets": ["系统外力冲量", "动量守恒条件"],
            "applicability": ["碰撞时间极短", "系统外力冲量可忽略"],
            "exceptions": ["非弹性碰撞中机械能通常不守恒"],
            "authority_level": authority,
        }
    )


def projection(*units):
    return {
        "status": "ready",
        "projection_version": "wuli-evidence-unit-shadow-v1",
        "units": list(units),
    }


def reflection(*, facets=None, conflicts=None, evidence_ids=None):
    return {
        "status": "completed",
        "message": "semantic check completed",
        "coverage": [
            {
                "need_id": "N1",
                "covered_facets": (
                    ["系统外力冲量", "动量守恒条件"]
                    if facets is None
                    else facets
                ),
                "evidence_bindings": [
                    {
                        "evidence_id": evidence_id,
                        "covered_facets": (
                            ["系统外力冲量", "动量守恒条件"]
                            if facets is None
                            else facets
                        ),
                        "reason": "候选直接绑定这些 facet",
                    }
                    for evidence_id in (
                        ["EU1"] if evidence_ids is None else evidence_ids
                    )
                ],
                "hard_conflicts": (
                    []
                    if conflicts is None
                    else [
                        {"evidence_id": "EU1", "conflict": item}
                        for item in conflicts
                    ]
                ),
                "condition_verdict": "compatible",
                "forbidden_conflict_checks": [
                    {
                        "conflict": "把机械能守恒当作必要条件",
                        "verdict": "absent",
                        "reason": "当前题未声明该错误条件",
                    }
                ],
                "not_required": False,
                "reason": "原文明确给出条件",
            }
        ],
    }


class EvidenceAgentTest(unittest.TestCase):
    def setUp(self):
        self.problem = "小车与木块发生非弹性碰撞，求共同速度。"
        self.blueprint = {"schema": "test-blueprint.v1", "question_targets": ["Q1"]}
        self.need = need()
        self.unit = unit()
        self.projection = projection(self.unit)
        self.pool = evidence_agent.retrieve_single_route_candidates(
            self.projection, [self.need]
        )

    def build(self, semantic):
        return evidence_agent.build_evidence_agent_run(
            problem=self.problem,
            blueprint=self.blueprint,
            retrieval_needs=[self.need],
            projection=self.projection,
            candidate_pool=self.pool,
            reflection=semantic,
        )

    def test_single_route_retrieval_is_deterministic_and_has_no_rrf(self):
        second = evidence_agent.retrieve_single_route_candidates(
            self.projection, [self.need]
        )
        self.assertEqual(self.pool, second)
        self.assertEqual(self.pool["route"], "single-route-bypass")
        self.assertEqual(self.pool["candidates"][0]["evidence_id"], "EU1")
        self.assertNotIn("rrf", json.dumps(self.pool).lower())

    def test_full_semantic_coverage_passes_deterministic_gate(self):
        run = self.build(reflection())
        self.assertEqual(run["status"], "sufficient")
        self.assertEqual(run["evidence_set"][0]["evidence_id"], "EU1")
        self.assertEqual(run["coverage"][0]["status"], "covered")
        self.assertEqual(
            run["retrieval_trace"][0]["stop_reason"],
            "all-required-needs-covered",
        )

    def test_model_cannot_self_approve_a_missing_facet(self):
        run = self.build(reflection(facets=["系统外力冲量"]))
        self.assertEqual(run["status"], "insufficient")
        self.assertEqual(run["coverage"][0]["status"], "missing")
        self.assertEqual(run["coverage"][0]["missing_facets"], ["动量守恒条件"])
        self.assertEqual(run["evidence_set"], [])

    def test_hard_conflict_rejects_candidate_from_evidence_set(self):
        run = self.build(
            reflection(conflicts=["候选把机械能守恒误写成动量守恒前提"])
        )
        self.assertEqual(run["status"], "insufficient")
        self.assertEqual(run["coverage"][0]["status"], "conflicted")
        self.assertEqual(run["coverage"][0]["evidence_ids"], [])
        self.assertEqual(run["evidence_set"], [])

    def test_diagnostic_target_is_not_an_applicability_conflict(self):
        diagnostic_need = dict(self.need)
        diagnostic_need.update(
            {
                "purpose": "false_friend_check",
                "diagnostic_targets": ["中途改变角度正方向并直接相加"],
            }
        )
        semantic = reflection(
            conflicts=[
                "当前待核对解答中途改变角度正方向并直接相加，正是证据要纠正的错误"
            ]
        )
        semantic["coverage"][0]["condition_verdict"] = "incompatible"
        run = evidence_agent.build_evidence_agent_run(
            problem="某解答中途改变角度正方向并直接相加，请核对。",
            blueprint=self.blueprint,
            retrieval_needs=[diagnostic_need],
            projection=self.projection,
            candidate_pool=evidence_agent.retrieve_single_route_candidates(
                self.projection, [diagnostic_need]
            ),
            reflection=semantic,
        )
        self.assertEqual(run["status"], "sufficient")
        self.assertEqual(run["coverage"][0]["status"], "covered")
        self.assertEqual(run["coverage"][0]["hard_conflicts"], [])
        self.assertEqual(
            run["usage_ledger"]["entries"][0]["usage"],
            "false_friend_warning",
        )

    def test_boundary_check_diagnostic_target_has_the_same_corrective_scope(self):
        diagnostic_need = dict(self.need)
        diagnostic_need.update(
            {
                "purpose": "boundary_check",
                "forbidden_conflicts": [],
                "diagnostic_targets": [
                    "求得的垂直时刻不在区间内却不检查端点"
                ],
            }
        )
        semantic = reflection(
            conflicts=[
                "求得的垂直时刻不在区间内却不检查端点"
            ]
        )
        semantic["coverage"][0]["condition_verdict"] = "incompatible"
        semantic["coverage"][0]["forbidden_conflict_checks"] = []
        run = evidence_agent.build_evidence_agent_run(
            problem="垂直时刻早于观察区间，应该如何检查区间端点？",
            blueprint=self.blueprint,
            retrieval_needs=[diagnostic_need],
            projection=self.projection,
            candidate_pool=evidence_agent.retrieve_single_route_candidates(
                self.projection, [diagnostic_need]
            ),
            reflection=semantic,
        )
        self.assertEqual(run["status"], "sufficient")
        self.assertEqual(run["coverage"][0]["status"], "covered")
        self.assertEqual(run["coverage"][0]["hard_conflicts"], [])
        self.assertEqual(
            run["usage_ledger"]["entries"][0]["usage"],
            "condition_warning",
        )

    def test_uncertain_true_forbidden_conflict_remains_fail_closed(self):
        semantic = reflection()
        semantic["coverage"][0]["forbidden_conflict_checks"][0][
            "verdict"
        ] = "uncertain"
        run = self.build(semantic)
        self.assertEqual(run["status"], "insufficient")
        self.assertEqual(run["coverage"][0]["status"], "conflicted")
        self.assertIn(
            "forbidden-conflict-uncertain:把机械能守恒当作必要条件",
            run["coverage"][0]["hard_conflicts"],
        )

    def test_authority_threshold_is_programmatic(self):
        weak_need = need(minimum_authority="A")
        pool = evidence_agent.retrieve_single_route_candidates(
            self.projection, [weak_need]
        )
        run = evidence_agent.build_evidence_agent_run(
            problem=self.problem,
            blueprint=self.blueprint,
            retrieval_needs=[weak_need],
            projection=self.projection,
            candidate_pool=pool,
            reflection=reflection(),
        )
        self.assertEqual(run["status"], "insufficient")
        self.assertEqual(run["coverage"][0]["status"], "conflicted")
        self.assertIn(
            "authority-below-minimum:EU1",
            run["coverage"][0]["hard_conflicts"],
        )

    def test_unknown_candidate_and_invented_facet_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside candidate pool"):
            evidence_agent.normalize_reflection(
                reflection(evidence_ids=["HALLUCINATED"]),
                retrieval_needs=[self.need],
                candidate_pool=self.pool,
            )
        with self.assertRaisesRegex(ValueError, "invents a facet"):
            evidence_agent.normalize_reflection(
                reflection(facets=["答案正确"]),
                retrieval_needs=[self.need],
                candidate_pool=self.pool,
            )

    def test_projection_failure_returns_unavailable_without_model_output(self):
        unavailable = {"status": "unavailable", "reason": "knowledge-store-stale", "units": []}
        pool = evidence_agent.retrieve_single_route_candidates(
            unavailable, [self.need]
        )
        run = evidence_agent.build_evidence_agent_run(
            problem=self.problem,
            blueprint=self.blueprint,
            retrieval_needs=[self.need],
            projection=unavailable,
            candidate_pool=pool,
            reflection=None,
        )
        self.assertEqual(run["status"], "unavailable")
        self.assertEqual(run["insufficient_evidence"], "knowledge-store-stale")

    def test_source_kind_filter_limits_model_visible_projection(self):
        approved = unit(evidence_id="EU2")
        curated = unit(
            evidence_id="EU1",
            authority="A",
            source_kind="curated_technique",
        )
        with mock.patch.object(
            evidence_agent.knowledge_store,
            "load_evidence_unit_projection",
            return_value=projection(approved, curated),
        ):
            visible, _pool = evidence_agent.load_projection_and_candidates(
                Path("/tmp/library"),
                [self.need],
                exclude_entry_id="current",
                source_kinds=("curated_technique",),
            )
        self.assertEqual(
            [item["evidence_id"] for item in visible["units"]], ["EU1"]
        )
        self.assertEqual(visible["unit_count_before_source_filter"], 2)

    def test_evaluation_overlay_is_explicit_and_collision_checked(self):
        approved = unit(evidence_id="EU2")
        overlay = unit(
            evidence_id="EU-OVERLAY",
            authority="A",
            source_kind="curated_technique",
        )
        with mock.patch.object(
            evidence_agent.knowledge_store,
            "load_evidence_unit_projection",
            return_value=projection(approved),
        ):
            default, _ = evidence_agent.load_projection_and_candidates(
                Path("/tmp/library"),
                [self.need],
                exclude_entry_id="current",
                source_kinds=("curated_technique",),
            )
            explicit, _ = evidence_agent.load_projection_and_candidates(
                Path("/tmp/library"),
                [self.need],
                exclude_entry_id="current",
                source_kinds=("curated_technique",),
                projection_overlay=[overlay],
            )
            with self.assertRaisesRegex(ValueError, "collides"):
                evidence_agent.load_projection_and_candidates(
                    Path("/tmp/library"),
                    [self.need],
                    exclude_entry_id="current",
                    projection_overlay=[approved],
                )
        self.assertEqual(default["units"], [])
        self.assertEqual(
            [item["evidence_id"] for item in explicit["units"]],
            ["EU-OVERLAY"],
        )
        self.assertEqual(explicit["overlay_unit_count"], 1)

    def test_optional_only_need_short_circuits_to_not_needed(self):
        optional = need(criticality="optional")
        run = evidence_agent.build_evidence_agent_run(
            problem=self.problem,
            blueprint=self.blueprint,
            retrieval_needs=[optional],
            projection=self.projection,
            candidate_pool=evidence_agent.retrieve_single_route_candidates(
                self.projection, [optional]
            ),
            reflection=None,
        )
        self.assertEqual(run["status"], "not_needed")
        self.assertEqual(run["retrieval_trace"], [])

    def test_gateway_executes_read_only_structured_atomic_task(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = Path(directory) / "entries" / "entry-1"
            entry.mkdir(parents=True)
            (entry / "problem.md").write_text(self.problem, encoding="utf-8")
            task = evidence_agent.build_shadow_task(
                entry,
                problem=self.problem,
                blueprint=self.blueprint,
                retrieval_needs=[self.need],
                candidate_pool=self.pool,
            )

            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {
                            "structured_output": reflection(),
                            "usage": {"input_tokens": 20, "output_tokens": 10},
                        },
                        ensure_ascii=False,
                    ),
                    stderr="",
                )

            gateway = AgentGateway(
                environ={"TEACHER_CONSOLE_AGENT_PROVIDER": "claude"},
                which=lambda name: f"/fake/{name}" if name == "claude" else None,
                run=runner,
            )
            result = gateway.run(
                task,
                materializer=evidence_agent.reflection_materializer(
                    [self.need], self.pool
                ),
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(task["kind"], "analysis.generate")
        self.assertEqual(task["evidence_stage"], "evidence.build")
        self.assertEqual(task["model_config"]["effort"], "low")
        self.assertEqual(commands[0][commands[0].index("--effort") + 1], "low")
        self.assertEqual(
            result["materialization"]["payload"]["coverage"][0]["need_id"],
            "N1",
        )


if __name__ == "__main__":
    unittest.main()
