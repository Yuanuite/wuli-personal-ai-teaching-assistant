#!/usr/bin/env python3

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


CONSOLE_ROOT = Path(__file__).resolve().parents[1]
if str(CONSOLE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONSOLE_ROOT))
SERVER_SPEC = importlib.util.spec_from_file_location(
    "analysis_routing_server", CONSOLE_ROOT / "server.py"
)
server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(server)
import analysis_routing
import w3_rendering
import w3r_contract

W3R_FIXTURE = (
    CONSOLE_ROOT / "tests" / "fixtures" / "w3r" / "verified-multi-target.json"
)


class AnalysisRoutingServerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.entry = self.library / "entries" / "gray-entry"
        (self.library / "config").mkdir(parents=True)
        self.entry.mkdir(parents=True)
        (self.entry / "problem.md").write_text(
            "粒子第一次进入区域后，求所有可能的返回时刻。", encoding="utf-8"
        )
        (self.entry / "record.json").write_text(
            json.dumps({"schema_version": 1, "entry_id": self.entry.name}),
            encoding="utf-8",
        )
        (self.entry / "pipeline.json").write_text(
            json.dumps({"schema_version": 1, "entry_id": self.entry.name}),
            encoding="utf-8",
        )
        self.config_path = self.library / "config" / "w3-production-routing.json"
        self.config_path.write_text(
            json.dumps({
                "schema_version": 1,
                "policy_version": "wuli-analysis-adaptive-v1",
                "mode": "gray",
                "gray_entry_ids": [self.entry.name],
                "max_agent_calls": 6,
                "max_teacher_focus": 2,
                "max_latency_seconds": 900,
            }),
            encoding="utf-8",
        )
        self.core_config_path = (
            self.library / "config" / "analysis-production-routing.json"
        )
        self.core_config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "policy_version": "wuli-core-first-routing-v1",
                    "mode": "legacy-adaptive",
                    "max_latency_seconds": 90,
                }
            ),
            encoding="utf-8",
        )
        self.handler = object.__new__(server.Handler)

    def tearDown(self):
        self.temporary.cleanup()

    def test_failed_w3_falls_back_to_w2_and_records_reason(self):
        self.handler.run_w3_shadow_analysis = lambda _entry, _data: {
            "status": "failed",
            "message": "stage failed",
            "stages": [{"stage": "decompose", "status": "failed"}],
        }
        self.handler.run_analysis = lambda _entry, _data: {"status": "completed"}
        with (
            mock.patch.object(server, "LIBRARY", self.library),
            mock.patch.object(server, "W3_ROUTING_CONFIG_PATH", self.config_path),
        ):
            result = self.handler.run_adaptive_analysis(self.entry, {})
        routing = result["adaptive_routing"]
        self.assertEqual(routing["selected_route"], "w2")
        self.assertTrue(routing["observed_metrics"]["fallback_used"])
        self.assertIn(
            "w3-request-not-completed", routing["fallback"]["reason_codes"]
        )
        self.assertEqual(routing["fallback"]["w2_status"], "completed")

    def test_core_first_config_bypasses_legacy_w2_w3_router(self):
        self.core_config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "policy_version": "wuli-core-first-routing-v1",
                    "mode": "core-first",
                    "max_latency_seconds": 90,
                }
            ),
            encoding="utf-8",
        )
        self.handler.run_core_analysis = mock.Mock(
            return_value={"status": "completed", "stages": []}
        )
        self.handler.run_w3_shadow_analysis = mock.Mock(
            side_effect=AssertionError("legacy W3 must not run")
        )
        self.handler.run_analysis = mock.Mock(
            side_effect=AssertionError("legacy W2 must not run")
        )
        with mock.patch.object(server, "LIBRARY", self.library):
            result = self.handler.run_adaptive_analysis(
                self.entry, {"method_profile": "olympiad_official"}
            )
        self.assertEqual(result["adaptive_routing"]["selected_route"], "core")
        self.assertEqual(
            result["adaptive_routing"]["limits"]["max_agent_calls"], 1
        )
        self.handler.run_core_analysis.assert_called_once()
        self.handler.run_w3_shadow_analysis.assert_not_called()
        self.handler.run_analysis.assert_not_called()

    def test_stop_policy_does_not_repeat_failed_w3_as_w2(self):
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        config["w3_failure_policy"] = "stop"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")
        self.handler.run_w3_shadow_analysis = lambda _entry, _data: {
            "status": "failed",
            "message": "stage failed",
            "stages": [{"stage": "decompose", "status": "failed"}],
        }
        self.handler.run_analysis = mock.Mock(
            side_effect=AssertionError("W2 must not run after a failed W3")
        )
        with (
            mock.patch.object(server, "LIBRARY", self.library),
            mock.patch.object(server, "W3_ROUTING_CONFIG_PATH", self.config_path),
        ):
            result = self.handler.run_adaptive_analysis(self.entry, {})
        routing = result["adaptive_routing"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(routing["selected_route"], "w3")
        self.assertFalse(routing["observed_metrics"]["fallback_used"])
        self.assertEqual(routing["fallback"]["to"], "none")
        self.assertEqual(
            routing["fallback"]["policy"], "stop-after-w3-failure"
        )
        self.handler.run_analysis.assert_not_called()

    def test_valid_w3_candidate_is_promoted_for_teacher_review(self):
        report = {
            "policy": "wuli-w3-shadow-v1",
            "screen": {"decision": "decompose"},
            "solver_a": {"status": "completed"},
            "recommended_student_solution": (
                "## 答案速览\n\n- A\n\n## 一眼识别\n\n"
                "- **最短主线**：受力分析\n\n## 详细解答\n\n"
                "### 第 1 步\n\nF=ma。\n\n## 易错点\n\n- 方向\n\n"
                "## 30 秒自测\n\n能否复算？\n"
            ),
            "blueprint": {
                "physical_stages": [
                    {"id": "S1", "label": "受力分析"},
                    {"id": "S2", "label": "牛顿第二定律"},
                ],
                "verification_obligations": [{"check": "复算 F=ma"}],
            },
            "stage_warnings": [],
            "metrics": {"teacher_focus_count": 0},
        }
        request = {
            "status": "completed",
            "requested_at": "2026-07-29T00:00:00+08:00",
            "stages": [{"stage": "solver-a", "status": "completed"}],
            "report": report,
        }
        self.handler.run_w3_shadow_analysis = lambda _entry, _data: request
        with (
            mock.patch.object(server, "LIBRARY", self.library),
            mock.patch.object(server, "W3_ROUTING_CONFIG_PATH", self.config_path),
            mock.patch.object(server, "validate_answer_candidate", return_value=[]),
            mock.patch.object(
                server,
                "mark_answer_needs_review",
                return_value={"state": {"state": "needs-answer-review"}},
            ),
            mock.patch.object(server, "_save_agent_baseline"),
            mock.patch.object(server, "assess_entry_difficulty", return_value={}),
            mock.patch.object(
                server, "resolve_model_id_for_task", return_value="model"
            ),
            mock.patch.object(
                server, "archive_agent_result", return_value={"event_id": "event"}
            ),
        ):
            result = self.handler.run_adaptive_analysis(self.entry, {})
        self.assertEqual(result["adaptive_routing"]["selected_route"], "w3")
        self.assertIsNone(result["adaptive_routing"]["fallback"])
        self.assertEqual(
            (self.entry / "solution.md").read_bytes(),
            (self.entry / "teacher-solution.md").read_bytes(),
        )
        self.assertIn(
            "教师审计",
            (self.entry / "teacher-solution.md").read_text(encoding="utf-8"),
        )
        promoted_record = json.loads(
            (self.entry / "record.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            promoted_record["knowledge_points"],
            ["受力分析", "牛顿第二定律"],
        )

    def test_gate_passed_w3r_is_materialized_and_keeps_teacher_approval_gate(self):
        source = json.loads(W3R_FIXTURE.read_text(encoding="utf-8"))
        brief = w3r_contract.build_w3r_brief(
            source["problem"], source["blueprint"], source["proof_package"]
        )["brief"]
        render = w3_rendering.render_w3r(brief)
        report = {
            "policy": "wuli-w3-shadow-v1",
            "screen": {"decision": "decompose"},
            "solver_a": {"status": "completed"},
            "recommended_student_solution": (
                "## 答案速览\n\n- A\n\n## 一眼识别\n\n"
                "- **最短主线**：受力分析\n\n## 详细解答\n\n"
                "### 第 1 步\n\nF=ma。\n\n## 易错点\n\n- 方向\n\n"
                "## 30 秒自测\n\n能否复算？\n"
            ),
            "blueprint": source["blueprint"],
            "stage_warnings": [],
            "metrics": {"teacher_focus_count": 0},
            "claim_evidence_shadow": {"aggregation": {"status": "VERIFIED"}},
            "w3r_shadow": {
                "status": "completed",
                "brief_fingerprint": w3r_contract.brief_fingerprint(brief),
                "brief": brief,
                "render_result": render,
            },
        }
        request = {
            "status": "completed",
            "stages": [{"stage": "solver-a", "status": "completed"}],
            "report": report,
        }
        w3r_config_path = (
            self.library / "config" / server.W3R_ROUTING_CONFIG_NAME
        )
        w3r_config_path.write_text(json.dumps({
            "schema_version": 1,
            "policy_version": analysis_routing.W3R_POLICY_VERSION,
            "mode": "gray",
            "gray_entry_ids": [self.entry.name],
            "evidence": {
                **analysis_routing.DEFAULT_W3R_EVIDENCE,
                "report_digest": "sha256:" + "a" * 64,
                "paired_case_count": 2,
                "teacher_reviewed_case_count": 2,
                "final_answer_fidelity": 1.0,
                "claim_support_coverage": 1.0,
                "condition_retention": 1.0,
                "target_coverage": 1.0,
                "latex_validity": 1.0,
                "unsupported_claim_rate": 0.0,
                "teacher_readability_preference": 1.0,
                "teacher_edit_rate_non_regression": True,
            },
        }), encoding="utf-8")
        self.handler.run_w3_shadow_analysis = lambda _entry, _data: request
        with (
            mock.patch.object(server, "LIBRARY", self.library),
            mock.patch.object(server, "W3_ROUTING_CONFIG_PATH", self.config_path),
            mock.patch.object(server, "validate_answer_candidate", return_value=[]),
            mock.patch.object(
                server,
                "mark_answer_needs_review",
                return_value={"state": {"state": "needs-answer-review"}},
            ),
            mock.patch.object(server, "_save_agent_baseline"),
            mock.patch.object(server, "assess_entry_difficulty", return_value={}),
            mock.patch.object(server, "resolve_model_id_for_task", return_value="model"),
            mock.patch.object(
                server, "archive_agent_result", return_value={"event_id": "event"}
            ),
        ):
            result = self.handler.run_adaptive_analysis(self.entry, {})
        renderer = result["adaptive_routing"]["renderer"]
        self.assertEqual(renderer["selected_renderer"], "w3r")
        self.assertEqual(result["resulting_state"], "needs-answer-review")
        self.assertEqual(result["w3r_review"]["gate_status"], "pass")
        self.assertNotIn(
            "claim_span_map",
            json.dumps(result["w3r_review"], ensure_ascii=False),
        )
        student = (self.entry / "student-solution.md").read_text(encoding="utf-8")
        teacher = (self.entry / "teacher-solution.md").read_text(encoding="utf-8")
        self.assertIn("## 建模与符号", student)
        self.assertNotIn("教师审计", student)
        self.assertIn("教师审计（不公开）", teacher)


if __name__ == "__main__":
    unittest.main()
