import json
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest import mock

import analysis_artifacts
import diagram_plugins
import server
from agent_gateway import AgentGateway
from visual_facts import normalize_payload


class MimoDeepseekCollaborationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.entry = Path(self.temporary.name) / "entry"
        (self.entry / "assets").mkdir(parents=True)
        raw = {
            "schema": "wuli.visual-facts.v1",
            "source_fingerprint": "sha256:" + "a" * 64,
            "reviewed_text": "物块沿斜面运动",
            "printed_facts": ["物块从静止释放"],
            "diagram_facts": [],
            "handwriting": [],
            "uncertainties": [],
            "model_identity": {"model_id": "mimo-v2.5-flash", "provider": "openai-compatible"},
        }
        facts = normalize_payload(raw, str(raw["source_fingerprint"]))
        (self.entry / "visual-facts.json").write_text(json.dumps(facts), encoding="utf-8")
        (self.entry / "problem.md").write_text("# 物理题\n", encoding="utf-8")
        (self.entry / "record.json").write_text("{}\n", encoding="utf-8")
        (self.entry / analysis_artifacts.EXPLANATION_PATH).write_text(
            analysis_artifacts.render_explanation_diagram(
                "关系", ["读图", "求解"], plugin_id=diagram_plugins.FLOWCHART_PLUGIN_ID
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_analysis_task_carries_facts_and_system_owned_provenance(self):
        task = server.analysis_task(self.entry, "解题")
        self.assertIn("visual-facts.json", task["input_paths"])
        self.assertIn("visual-facts.json", task["structured_context_paths"])
        self.assertNotIn("svg-provenance.json", task["allowed_paths"])
        self.assertIn("不得改写或抵触", task["prompt"])

    def test_physics_diagram_is_a_separate_typed_task(self):
        task = server.physics_diagram_task(
            self.entry,
            model_config={"id": "deepseek-v4-flash-api", "provider": "openai-compatible"},
        )
        self.assertEqual(task["kind"], "diagram.scene")
        self.assertEqual(task["output_contract"]["name"], "wuli.physics-diagram-scene.v1")
        self.assertIn("visual-facts.json", task["structured_context_paths"])
        self.assertIn("student-solution.md", task["structured_context_paths"])
        self.assertIn(".agent-context/diagram-obligations.json", task["structured_context_paths"])
        self.assertNotIn("teacher-solution.md", task["structured_context_paths"])
        self.assertIn("不要输出流程图", task["prompt"])
        self.assertIn("physics-diagram-gate.json", task["allowed_paths"])

    def test_failed_scene_revision_uses_bounded_patch_contract(self):
        candidate = {
            "status": "completed",
            "message": "candidate",
            "title": "候选图",
            "panels": [],
            "regions": [],
            "objects": [],
            "paths": [],
            "annotations": [],
            "omissions": [],
        }
        diagnostics = {
            "schema": "wuli.physics-diagram-diagnostics.v1",
            "status": "failed",
            "repairable": True,
            "diagnostics": [{"code": "scene.panel-count", "path": "/panels"}],
        }
        task = server.physics_diagram_patch_task(
            self.entry,
            candidate,
            diagnostics,
            model_config={"id": "deepseek-v4-flash-api", "provider": "openai-compatible"},
        )
        self.assertEqual(task["kind"], "diagram.scene")
        self.assertEqual(task["output_contract"]["name"], "wuli.physics-diagram-scene-patch.v1")
        self.assertIn("不得重新生成整张场景", task["prompt"])
        self.assertIn("teacher-solution.md", task["input_paths"])
        self.assertIn("solution.md", task["input_paths"])
        self.assertNotIn("student-solution.md", task["structured_context_paths"])
        self.assertEqual(
            task["structured_context_paths"],
            [
                ".agent-context/rejected-physics-diagram-scene.json",
                ".agent-context/physics-diagram-diagnostics.json",
                ".agent-context/diagram-obligations.json",
            ],
        )
        self.assertEqual(
            task["context_payloads"][".agent-context/rejected-physics-diagram-scene.json"],
            candidate,
        )

    def test_diagram_orchestrator_runs_at_most_one_patch_retry(self):
        candidate = {
            "status": "completed",
            "message": "candidate",
            "title": "候选图",
            "panels": [],
            "regions": [],
            "objects": [],
            "paths": [],
            "annotations": [],
            "omissions": [],
        }
        report = {
            "schema": "wuli.physics-diagram-diagnostics.v1",
            "status": "failed",
            "repairable": True,
            "diagnostics": [{"code": "scene.panel-count", "path": "/panels"}],
        }
        initial = {
            "status": "failed",
            "attempts": [
                {
                    "materialization": {
                        "status": "rejected",
                        "rejected_candidate": candidate,
                        "diagnostic_report": report,
                    }
                }
            ],
        }
        recovered = {"status": "completed", "attempts": [{"status": "completed"}]}
        with mock.patch.object(server, "run_agent_gateway", side_effect=[initial, recovered]) as run:
            result = server.run_physics_diagram_gateway(
                self.entry,
                routing_tier="auto",
                model_config={"id": "deepseek-v4-flash-api", "provider": "openai-compatible"},
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1].args[1]["output_contract"]["name"],
            "wuli.physics-diagram-scene-patch.v1",
        )
        self.assertFalse(run.call_args_list[0].kwargs["bounded_failure_repair"])
        self.assertFalse(run.call_args_list[1].kwargs["bounded_failure_repair"])
        self.assertEqual(result["diagram_repair"]["status"], "recovered")
        self.assertEqual(result["diagram_repair"]["retry_count"], 1)

    def test_flowchart_plugin_is_never_implicit(self):
        render = getattr(analysis_artifacts, "render_explanation_diagram")
        with self.assertRaisesRegex(TypeError, "plugin_id"):
            render("关系", ["读图", "求解"])

    def test_provenance_binds_mimo_facts_and_deepseek_route(self):
        result = server._stage_svg_provenance(
            self.entry,
            model_config={"id": "deepseek-v4-flash-api", "provider": "openai-compatible"},
            generation_fingerprint="b" * 64,
        )
        assert result is not None
        self.assertEqual(result["model_identity"]["model_id"], "deepseek-v4-flash-api")
        self.assertTrue(result["visual_facts_fingerprint"].startswith("sha256:"))
        self.assertTrue((self.entry / "svg-provenance.json").is_file())

    def test_mimo_direct_svg_generation_interface_is_removed(self):
        self.assertFalse(hasattr(AgentGateway, "generate_physics_diagram"))


if __name__ == "__main__":
    unittest.main()
