import importlib.util
import unittest
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "openai_compatible_agent_adapter",
    ROOT / "teacher-console" / "providers" / "openai_compatible_agent_adapter.py",
)
assert SPEC is not None
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

    def test_direct_model_config_overrides_tier_mapping(self):
        selected = adapter.select_model(
            {
                "kind": "answer.revise",
                "routing_tier": "economy",
                "model_config": {
                    "provider": "openai-compatible",
                    "model": "teacher-picked-model",
                    "model_tier": "custom",
                },
            },
            self.env,
        )
        self.assertEqual(selected, ("teacher-picked-model", "custom", "economy", ""))

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

    def test_deepseek_direct_api_uses_server_default_thinking_and_bounds_output(self):
        options = adapter.request_options(
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            {},
        )
        self.assertNotIn("thinking", options)
        self.assertEqual(options["response_format"], {"type": "json_object"})
        self.assertEqual(options["max_tokens"], 6000)

    def test_explicit_thinking_override_is_preserved(self):
        options = adapter.request_options(
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            {"TEACHER_CONSOLE_AGENT_API_THINKING": "enabled"},
        )
        self.assertEqual(options["thinking"], {"type": "enabled"})

    def test_parse_content_repairs_only_unescaped_inner_quotes(self):
        parsed = adapter.parse_content('{"status":"completed","message":"use "quoted" term","targets":[]}')
        self.assertEqual(parsed["message"], 'use "quoted" term')
        with self.assertRaises(ValueError):
            adapter.parse_content('{"status":"completed" "message":"missing comma"}')

    def test_large_solver_contract_is_split_and_merged_losslessly(self):
        fields = {
            field: {"type": ["array", "null"]}
            for field in (
                "targets",
                "stage_results",
                "stage_interfaces",
                "stage_transitions",
                "option_verdicts",
            )
        }
        fields.update({
            "status": {"type": "string"},
            "message": {"type": "string"},
            "blueprint_audit": {"type": ["object", "null"]},
        })
        fields["targets"] = {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "properties": {
                    "final_answer": {"type": "string"},
                    "supporting_relations": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 12,
                    },
                },
            },
        }
        contract = {
            "name": "wuli.solution-reasoning.v2.1.solver-a",
            "schema": {
                "type": "object",
                "properties": fields,
                "required": list(fields),
            },
        }
        core_contract, interface_contract = adapter.compact_solution_contracts(contract)
        self.assertNotIn("stage_interfaces", core_contract["schema"]["properties"])
        target_schema = core_contract["schema"]["properties"]["targets"]["items"]
        self.assertEqual(
            target_schema["properties"]["final_answer"]["maxLength"],
            400,
        )
        self.assertEqual(
            target_schema["properties"]["supporting_relations"]["maxItems"],
            4,
        )
        self.assertEqual(
            set(interface_contract["schema"]["properties"]),
            {"stage_results", "stage_interfaces", "stage_transitions"},
        )
        merged = adapter.merge_compact_solution(
            {
                "status": "completed",
                "message": "ok",
                "targets": [{"id": "T1"}],
                "stage_results": [],
                "option_verdicts": [],
                "blueprint_audit": {},
            },
            {
                "stage_results": [],
                "stage_interfaces": [
                    {
                        "stage_id": "S1",
                        "directions": {"θ": "逆时针为正", "+x": "向右"},
                        "entry_state": {"气柱高度": "25 cm", "水银柱高度": "25 cm"},
                        "exit_state": {"气柱高度": "50 cm"},
                        "required_entry_keys": ["气柱高度", "水银柱高度", "气柱高度"],
                        "carried_state_keys": ["气柱高度"],
                    },
                    {
                        "stage_id": "S2",
                        "directions": {"+x": "向右"},
                        "entry_state": {"气柱高度": "50 cm"},
                        "exit_state": {"气柱高度": "50 cm"},
                        "required_entry_keys": ["气柱高度"],
                        "carried_state_keys": ["气柱高度"],
                    },
                ],
                "stage_transitions": [
                    {
                        "from_stage": "unused",
                        "to_stage": "unused-too",
                        "state_mapping": [{"to_key": "velocity"}],
                        "introduced_entry_keys": ["velocity", "new_force"],
                    }
                ],
            },
        )
        self.assertEqual(merged["targets"], [{"id": "T1"}])
        self.assertEqual(merged["stage_results"], [])
        self.assertEqual(
            merged["stage_interfaces"][0]["directions"],
            {"theta": "逆时针为正", "+x": "向右"},
        )
        self.assertEqual(
            len(merged["stage_interfaces"][0]["required_entry_keys"]),
            2,
        )
        self.assertTrue(all(key.startswith("state_") for key in merged["stage_interfaces"][0]["entry_state"]))
        self.assertEqual(merged["stage_transitions"][0]["from_stage"], "S1")
        self.assertEqual(merged["stage_transitions"][0]["to_stage"], "S2")
        self.assertEqual(len(merged["stage_transitions"][0]["state_mapping"]), 1)
        self.assertEqual(set(merged), set(fields))


if __name__ == "__main__":
    unittest.main()
