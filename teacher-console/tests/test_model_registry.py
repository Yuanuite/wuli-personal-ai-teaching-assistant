import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "teacher-console"))

import kb  # noqa: E402
import model_registry  # noqa: E402


class ModelRegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        self.original_library = model_registry.LIBRARY
        model_registry.LIBRARY = self.library
        kb.write_json(
            self.library / "config" / "model-registry.json",
            {
                "schema_version": 1,
                "defaults": {
                    "economy": "cheap",
                    "expert": "deep",
                    "analysis.generate": "analysis-model",
                    "answer.revise": "revision-model",
                    "visualization.model": "visual-model",
                },
                "models": [
                    {
                        "id": "cheap",
                        "display_name": "Cheap",
                        "provider": "openai-compatible",
                        "base_url": "http://127.0.0.1:8000/v1",
                        "model": "cheap-model",
                        "capabilities": ["answer.revise"],
                    },
                    {
                        "id": "deep",
                        "display_name": "Deep",
                        "provider": "openai-compatible",
                        "base_url": "http://127.0.0.1:8000/v1",
                        "model": "deep-model",
                        "capabilities": ["analysis.generate", "answer.revise", "visualization.model"],
                    },
                    {
                        "id": "analysis-model",
                        "display_name": "Analysis",
                        "provider": "openai-compatible",
                        "base_url": "http://127.0.0.1:8000/v1",
                        "model": "analysis-model",
                        "capabilities": ["analysis.generate"],
                    },
                ],
            },
        )

    def tearDown(self):
        model_registry.LIBRARY = self.original_library
        self.temp.cleanup()

    def test_mode_defaults_resolve_to_registered_models(self):
        self.assertEqual(model_registry.resolve_model_id_for_task("answer.revise", "economy", "auto"), "cheap")
        self.assertEqual(model_registry.resolve_model_id_for_task("answer.revise", "expert", "auto"), "deep")
        self.assertEqual(
            model_registry.resolve_model_id_for_task("analysis.generate", "auto", "auto"), "analysis-model"
        )
        self.assertEqual(model_registry.resolve_model_id_for_task("analysis.generate", "auto", "deep"), "deep")

    def test_capability_mismatch_fails_before_agent_run(self):
        with self.assertRaisesRegex(ValueError, "暂不可用"):
            model_registry.model_config_for_task("analysis.generate", "cheap", "auto")

    def test_untested_model_is_unavailable_until_probe_passes(self):
        with self.assertRaisesRegex(ValueError, "not passed connection test|未测试|暂不可用"):
            model_registry.model_config_for_task("analysis.generate", "analysis-model", "auto")
        model_registry.update_model_probe_result(
            "analysis-model", {"live_probe": {"status": "passed", "provider": "openai-compatible", "reason": ""}}
        )
        config = model_registry.model_config_for_task("analysis.generate", "analysis-model", "auto")
        self.assertEqual(config["model"], "analysis-model")

    def test_claude_timeout_is_preserved_in_task_config(self):
        model_registry.save_model_registry_settings({
            "schema_version": 1,
            "defaults": {"analysis.generate": "claude-analysis"},
            "models": [{
                "id": "claude-analysis",
                "display_name": "Claude Analysis",
                "provider": "claude",
                "model": "claude-analysis",
                "timeout_seconds": "900",
                "capabilities": ["analysis.generate"],
            }],
        })
        model_registry.update_model_probe_result(
            "claude-analysis", {"live_probe": {"status": "passed", "provider": "claude", "reason": ""}}
        )
        config = model_registry.model_config_for_task("analysis.generate", "claude-analysis", "auto")
        self.assertEqual(config["timeout_seconds"], "900")

    def test_claude_compatible_backend_credentials_are_preserved_per_model(self):
        model_registry.save_model_registry_settings({
            "schema_version": 1,
            "defaults": {"analysis.generate": "deepseek-agent"},
            "models": [{
                "id": "deepseek-agent",
                "display_name": "DeepSeek via Claude Code",
                "provider": "claude",
                "base_url": "http://127.0.0.1:9001",
                "model": "deepseek-agent-model",
                "api_key": "local-agent-token",
                "api_key_env": "TEACHER_CONSOLE_AGENT_API_KEY",
                "capabilities": ["analysis.generate"],
            }],
        })
        model_registry.update_model_probe_result(
            "deepseek-agent", {"live_probe": {"status": "passed", "provider": "claude", "reason": ""}}
        )
        config = model_registry.model_config_for_task("analysis.generate", "deepseek-agent", "auto")
        self.assertEqual(config["base_url"], "http://127.0.0.1:9001")
        self.assertEqual(config["model"], "deepseek-agent-model")
        self.assertEqual(config["api_key"], "local-agent-token")
        public = model_registry.model_registry_public(kind="analysis.generate")["models"][0]
        self.assertEqual(public["base_url"], "http://127.0.0.1:9001")

    def test_api_key_is_saved_locally_but_not_returned_to_settings(self):
        saved = model_registry.save_model_registry_settings({
            "schema_version": 1,
            "defaults": {"analysis.generate": "remote-model"},
            "models": [
                {
                    "id": "remote-model",
                    "display_name": "Remote",
                    "provider": "openai-compatible",
                    "base_url": "http://127.0.0.1:9000/v1",
                    "model": "remote-model",
                    "api_key": "sk-local-secret",
                    "remote": False,
                    "capabilities": ["analysis.generate"],
                }
            ],
        })
        self.assertTrue(saved["models"][0]["api_key_configured"])
        raw = kb.load_json(self.library / "config" / "model-registry.json", {})
        self.assertEqual(raw["models"][0]["api_key"], "sk-local-secret")
        settings = model_registry.model_registry_settings()
        self.assertNotIn("api_key", settings["models"][0])
        self.assertTrue(settings["models"][0]["api_key_saved"])
        model_registry.update_model_probe_result(
            "remote-model", {"live_probe": {"status": "passed", "provider": "openai-compatible", "reason": ""}}
        )
        config = model_registry.model_config_for_task("analysis.generate", "remote-model", "auto")
        self.assertEqual(config["api_key"], "sk-local-secret")

    def test_blank_api_key_preserves_previous_saved_key(self):
        model_registry.save_model_registry_settings({
            "models": [
                {
                    "id": "remote-model",
                    "provider": "openai-compatible",
                    "base_url": "http://127.0.0.1:9000/v1",
                    "model": "remote-model",
                    "api_key": "sk-local-secret",
                    "remote": False,
                }
            ],
        })
        model_registry.save_model_registry_settings({
            "models": [
                {
                    "id": "remote-model",
                    "provider": "openai-compatible",
                    "base_url": "http://127.0.0.1:9000/v1",
                    "model": "remote-model",
                    "api_key": "",
                    "remote": False,
                }
            ],
        })
        raw = kb.load_json(self.library / "config" / "model-registry.json", {})
        self.assertEqual(raw["models"][0]["api_key"], "sk-local-secret")

    def test_model_change_invalidates_previous_probe(self):
        model_registry.save_model_registry_settings({
            "models": [
                {
                    "id": "analysis-model",
                    "provider": "openai-compatible",
                    "base_url": "http://127.0.0.1:8000/v1",
                    "model": "analysis-model",
                    "capabilities": ["analysis.generate"],
                }
            ],
        })
        model_registry.update_model_probe_result(
            "analysis-model", {"live_probe": {"status": "passed", "provider": "openai-compatible", "reason": ""}}
        )
        model_registry.save_model_registry_settings({
            "models": [
                {
                    "id": "analysis-model",
                    "provider": "openai-compatible",
                    "base_url": "http://127.0.0.1:8000/v1",
                    "model": "changed-model",
                    "capabilities": ["analysis.generate"],
                }
            ],
        })
        public = model_registry.model_registry_public(kind="analysis.generate")
        model = next(item for item in public["models"] if item["id"] == "analysis-model")
        self.assertFalse(model["available"])
        self.assertEqual(model["probe_status"], "untested")

    def test_example_registry_contains_litellm_aliases(self):
        example = json.loads((ROOT / "docs" / "model-registry.example.json").read_text(encoding="utf-8"))
        ids = {item["id"] for item in example["models"]}
        self.assertIn("wuli-economy", ids)
        self.assertIn("wuli-standard", ids)
        self.assertIn("wuli-expert", ids)
        self.assertIn("codex-visualization", ids)
        self.assertEqual(example["defaults"]["economy"], "wuli-economy")
        self.assertEqual(example["defaults"]["visualization.model"], "codex-visualization")


if __name__ == "__main__":
    unittest.main()
