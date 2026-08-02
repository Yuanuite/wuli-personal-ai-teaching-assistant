#!/usr/bin/env python3

import copy
import json
import sys
import unittest
from pathlib import Path


CONSOLE_ROOT = Path(__file__).resolve().parents[1]
if str(CONSOLE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONSOLE_ROOT))

import analysis_routing
import w3_rendering
import w3r_contract


W3R_FIXTURE = (
    CONSOLE_ROOT / "tests" / "fixtures" / "w3r" / "verified-multi-target.json"
)


def config(mode="gray", ids=None):
    return {
        "schema_version": 1,
        "policy_version": analysis_routing.POLICY_VERSION,
        "mode": mode,
        "gray_entry_ids": ids or [],
        "max_agent_calls": 6,
        "max_teacher_focus": 2,
        "max_latency_seconds": 900,
    }


def verified_w3r_report():
    source = json.loads(W3R_FIXTURE.read_text(encoding="utf-8"))
    built = w3r_contract.build_w3r_brief(
        source["problem"], source["blueprint"], source["proof_package"]
    )
    brief = built["brief"]
    render = w3_rendering.render_w3r(brief)
    return {
        "recommended_student_solution": "legacy answer",
        "blueprint": source["blueprint"],
        "claim_evidence_shadow": {"aggregation": {"status": "VERIFIED"}},
        "w3r_shadow": {
            "status": "completed",
            "brief_fingerprint": w3r_contract.brief_fingerprint(brief),
            "brief": brief,
            "render_result": render,
        },
    }


def w3r_config(mode="gray", ids=None, *, default_ready=False):
    evidence = {
        **analysis_routing.DEFAULT_W3R_EVIDENCE,
        "report_digest": "sha256:" + "a" * 64,
        "paired_case_count": 5 if default_ready else 2,
        "teacher_reviewed_case_count": 5 if default_ready else 2,
        "fresh_holdout_case_count": 5 if default_ready else 0,
        "fresh_holdout_target_count": 12 if default_ready else 0,
        "final_answer_fidelity": 1.0,
        "claim_support_coverage": 1.0,
        "condition_retention": 1.0,
        "target_coverage": 1.0,
        "latex_validity": 1.0,
        "unsupported_claim_rate": 0.0,
        "teacher_readability_preference": 0.6,
        "teacher_edit_rate_non_regression": True,
    }
    return {
        "schema_version": 1,
        "policy_version": analysis_routing.W3R_POLICY_VERSION,
        "mode": mode,
        "gray_entry_ids": ids or [],
        "evidence": evidence,
    }


class AnalysisRoutingTest(unittest.TestCase):
    def test_invalid_or_disabled_policy_fails_closed_to_w2(self):
        invalid = analysis_routing.decide(
            "第一次进入区域后求所有可能情况",
            entry_id="e1",
            config={"mode": "default"},
        )
        self.assertEqual(invalid["route"], "w2")
        self.assertEqual(invalid["reason"], "invalid-policy-config")
        disabled = analysis_routing.decide(
            "第一次进入区域后求所有可能情况",
            entry_id="e1",
            config=config("off"),
        )
        self.assertEqual(disabled["route"], "w2")

    def test_gray_requires_both_cohort_and_complexity_screen(self):
        complex_gray = analysis_routing.decide(
            "粒子第一次进入区域后，求所有可能的返回时刻。",
            entry_id="e1",
            config=config(ids=["e1"]),
        )
        self.assertEqual(complex_gray["route"], "w3")
        simple_gray = analysis_routing.decide(
            "质量为 m、速度为 v，求动能。",
            entry_id="e1",
            config=config(ids=["e1"]),
        )
        self.assertEqual(simple_gray["route"], "w2")
        outside = analysis_routing.decide(
            "粒子第一次进入区域后，求所有可能的返回时刻。",
            entry_id="e2",
            config=config(ids=["e1"]),
        )
        self.assertEqual(outside["reason"], "outside-gray-cohort")

    def test_default_routes_only_complex_problems_to_w3(self):
        complex_default = analysis_routing.decide(
            "粒子第一次进入区域后，求所有可能的返回时刻。",
            entry_id="outside-prior-gray-cohort",
            config=config("default"),
        )
        self.assertEqual(complex_default["route"], "w3")
        self.assertEqual(
            complex_default["reason"], "deterministic-complexity-screen"
        )
        simple_default = analysis_routing.decide(
            "质量为 m、速度为 v，求动能。",
            entry_id="outside-prior-gray-cohort",
            config=config("default"),
        )
        self.assertEqual(simple_default["route"], "w2")
        self.assertEqual(
            simple_default["reason"], "deterministic-low-risk-screen"
        )

    def test_w3_failure_policy_is_explicit_and_validated(self):
        raw = config("default")
        raw["w3_failure_policy"] = "stop"
        decision = analysis_routing.decide(
            "粒子第一次进入区域后，求所有可能的返回时刻。",
            entry_id="e1",
            config=raw,
        )
        self.assertEqual(decision["w3_failure_policy"], "stop")
        raw["w3_failure_policy"] = "retry-forever"
        invalid = analysis_routing.decide(
            "粒子第一次进入区域后，求所有可能的返回时刻。",
            entry_id="e1",
            config=raw,
        )
        self.assertEqual(invalid["route"], "w2")
        self.assertIn("w3_failure_policy", invalid["config_errors"][0])

    def test_readiness_rejects_warning_or_budget_overrun(self):
        decision = {
            "limits": {"max_agent_calls": 2, "max_teacher_focus": 2}
        }
        request = {
            "status": "completed",
            "stages": [{}, {}, {}],
            "report": {
                "policy": "wuli-w3-shadow-v1",
                "screen": {"decision": "decompose"},
                "solver_a": {"status": "completed"},
                "recommended_student_solution": (
                    "## 答案速览\n\n- A\n\n## 一眼识别\n\n"
                    "- **最短主线**：受力分析\n\n## 详细解答\n\n"
                    "### 第 1 步\n\nF=ma。\n\n## 易错点\n\n- 方向\n\n"
                    "## 30 秒自测\n\n能否复算？\n"
                ),
                "stage_warnings": [{"prompt": "check"}],
                "metrics": {"teacher_focus_count": 0},
            },
        }
        ready, errors = analysis_routing.production_readiness(request, decision)
        self.assertFalse(ready)
        self.assertIn("w3-stage-warning", errors)
        self.assertIn("w3-agent-call-limit-exceeded", errors)

    def test_candidate_files_keep_solution_equal_to_teacher(self):
        files = analysis_routing.candidate_files({
            "recommended_student_solution": "## 答案速览\n\n- A",
            "blueprint": {"verification_obligations": [{"check": "复算 A"}]},
        })
        self.assertEqual(files["solution.md"], files["teacher-solution.md"])
        self.assertIn("教师审计", files["teacher-solution.md"])
        self.assertIn("assets/explanatory.svg", files["student-solution.md"])
        self.assertNotIn("assets/explanatory.svg", files)

        plugin_files = analysis_routing.candidate_files(
            {
                "recommended_student_solution": "## 答案速览\n\n- A",
                "blueprint": {
                    "verification_obligations": [{"check": "复算 A"}],
                    "reasoning_steps": [
                        {"operation": "识别对象"},
                        {"operation": "建立关系"},
                    ],
                },
            },
            diagram_plugin_id="logic-flowchart",
        )
        self.assertIn("assets/explanatory.svg", plugin_files["student-solution.md"])
        self.assertIn("<svg", plugin_files["assets/explanatory.svg"])

    def test_w3r_invalid_shadow_or_outside_gray_stays_legacy(self):
        report = verified_w3r_report()
        invalid = analysis_routing.select_renderer(
            report, entry_id="e1", config={"mode": "gray"}
        )
        self.assertEqual(invalid["selected_renderer"], "legacy")
        shadow = analysis_routing.select_renderer(
            report, entry_id="e1", config=w3r_config("shadow")
        )
        self.assertEqual(shadow["reason"], "w3r-shadow-only")
        outside = analysis_routing.select_renderer(
            report, entry_id="e2", config=w3r_config(ids=["e1"])
        )
        self.assertEqual(outside["reason"], "w3r-outside-gray-cohort")

    def test_w3r_migrated_config_normalizes_without_errors(self):
        # A2.1: the truth-source config must match the code contract (policy
        # version + complete evidence field set); normalization keeps mode and
        # reports no errors, so the W3R selection is a deliberate choice rather
        # than a fail-closed accident.
        migrated = {
            "schema_version": 1,
            "policy_version": analysis_routing.W3R_POLICY_VERSION,
            "mode": "off",
            "gray_entry_ids": [],
            "evidence": dict(analysis_routing.DEFAULT_W3R_EVIDENCE),
        }
        config, errors = analysis_routing.normalize_w3r_config(migrated)
        self.assertEqual(errors, [])
        self.assertEqual(config["mode"], "off")
        self.assertEqual(set(config["evidence"]), set(analysis_routing.W3R_EVIDENCE_FIELDS))

    def test_w3r_legacy_contract_fails_closed_to_off(self):
        # The pre-migration config (wrong policy version + wrong evidence
        # fields) must fail closed to the default off config, never to default.
        legacy = {
            "schema_version": 1,
            "policy_version": "teacher-console.w3r-renderer.v1",
            "mode": "default",
            "gray_entry_ids": [],
            "evidence": {
                "max_claim_count": 30,
                "unsupported_claim_rate": 1.0,
                "teacher_readability_preference": 0.5,
                "teacher_edit_rate_non_regression": False,
            },
        }
        config, errors = analysis_routing.normalize_w3r_config(legacy)
        self.assertEqual(config["mode"], "off")
        self.assertTrue(errors)

    def test_w3r_gray_requires_rollout_and_candidate_hard_gates(self):
        report = verified_w3r_report()
        insufficient = w3r_config(ids=["e1"])
        insufficient["evidence"]["teacher_reviewed_case_count"] = 1
        selected = analysis_routing.select_renderer(
            report, entry_id="e1", config=insufficient
        )
        self.assertEqual(selected["selected_renderer"], "legacy")
        self.assertEqual(
            selected["reason"], "w3r-rollout-evidence-insufficient"
        )

        selected = analysis_routing.select_renderer(
            report, entry_id="e1", config=w3r_config(ids=["e1"])
        )
        self.assertEqual(selected["selected_renderer"], "w3r")
        self.assertFalse(selected["legacy_fallback_used"])

        tampered = copy.deepcopy(report)
        tampered["claim_evidence_shadow"]["aggregation"]["status"] = "PROVISIONAL"
        rejected = analysis_routing.select_renderer(
            tampered, entry_id="e1", config=w3r_config(ids=["e1"])
        )
        self.assertEqual(rejected["selected_renderer"], "legacy")
        self.assertIn(
            "w3r-proof-package-not-verified", rejected["readiness_errors"]
        )

        drifted = copy.deepcopy(report)
        drifted["w3r_shadow"]["render_result"]["student_solution_md"] = (
            drifted["w3r_shadow"]["render_result"]["student_solution_md"].replace(
                r"t=\pi m/(qB)", r"t=2\pi m/(qB)"
            )
        )
        rejected = analysis_routing.select_renderer(
            drifted, entry_id="e1", config=w3r_config(ids=["e1"])
        )
        self.assertEqual(rejected["selected_renderer"], "legacy")
        self.assertIn(
            "w3r-render-gate-report-stale", rejected["readiness_errors"]
        )

    def test_w3r_default_requires_fresh_holdout_and_teacher_non_regression(self):
        report = verified_w3r_report()
        blocked = analysis_routing.select_renderer(
            report, entry_id="e1", config=w3r_config("default")
        )
        self.assertEqual(blocked["selected_renderer"], "legacy")
        self.assertIn(
            "w3r-fresh-holdout-cases-insufficient",
            blocked["evidence_errors"],
        )
        selected = analysis_routing.select_renderer(
            report,
            entry_id="e1",
            config=w3r_config("default", default_ready=True),
        )
        self.assertEqual(selected["selected_renderer"], "w3r")

    def test_w3r_materialization_is_private_and_rollback_is_renderer_only(self):
        report = verified_w3r_report()
        gray = w3r_config(ids=["e1"])
        selected = analysis_routing.select_renderer(
            report, entry_id="e1", config=gray
        )
        files = analysis_routing.candidate_files(
            report, entry_id="e1", w3r_config=gray, selection=selected
        )
        student = files["student-solution.md"]
        teacher = files["teacher-solution.md"]
        self.assertEqual(files["solution.md"], teacher)
        self.assertIn("## 建模与符号", student)
        self.assertNotIn("教师审计", student)
        self.assertNotIn("sha256:", student)
        self.assertIn("教师审计（不公开）", teacher)

        rolled_back = analysis_routing.select_renderer(
            report, entry_id="e1", config=w3r_config("off")
        )
        legacy_files = analysis_routing.candidate_files(
            report,
            entry_id="e1",
            w3r_config=w3r_config("off"),
            selection=rolled_back,
        )
        self.assertIn("legacy answer", legacy_files["student-solution.md"])
        self.assertNotIn("## 建模与符号", legacy_files["student-solution.md"])


if __name__ == "__main__":
    unittest.main()
