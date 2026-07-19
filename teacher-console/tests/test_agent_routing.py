import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "openai_compatible_agent_adapter",
    ROOT / "teacher-console" / "providers" / "openai_compatible_agent_adapter.py",
)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class AgentRoutingTest(unittest.TestCase):
    def setUp(self):
        self.env = {
            "TEACHER_CONSOLE_AGENT_API_MODEL": "standard-model",
            "TEACHER_CONSOLE_AGENT_API_ECONOMY_MODEL": "cheap-model",
            "TEACHER_CONSOLE_AGENT_API_EXPERT_MODEL": "expert-model",
        }

    def test_explicit_economy_model(self):
        selected = adapter.select_model({"kind": "answer.revise", "routing_tier": "economy"}, self.env)
        self.assertEqual(selected, ("cheap-model", "economy", "economy", ""))

    def test_auto_visualization_prefers_expert(self):
        selected = adapter.select_model({"kind": "visualization.model", "routing_tier": "auto"}, self.env)
        self.assertEqual(selected, ("expert-model", "expert", "auto", ""))

    def test_missing_optional_model_falls_back_honestly(self):
        selected = adapter.select_model(
            {"kind": "answer.revise", "routing_tier": "economy"},
            {"TEACHER_CONSOLE_AGENT_API_MODEL": "standard-model"},
        )
        self.assertEqual(selected[:3], ("standard-model", "standard", "economy"))
        self.assertIn("降级", selected[3])

    def test_usage_is_normalized(self):
        self.assertEqual(
            adapter.normalized_usage({"usage": {"input_tokens": 8, "output_tokens": 3}}),
            {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
        )


if __name__ == "__main__":
    unittest.main()
