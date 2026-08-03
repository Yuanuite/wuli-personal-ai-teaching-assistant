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

import candidate_archive  # noqa: E402

SERVER_SPEC = importlib.util.spec_from_file_location(
    "teacher_console_server_failure_pipeline", ROOT / "teacher-console" / "server.py"
)
assert SERVER_SPEC is not None
teacher_console_server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(teacher_console_server)


class FakeGateway:
    def __init__(self, results):
        self.results = list(results)
        self.tasks = []

    def run(self, task, _validator):
        self.tasks.append(task)
        return self.results.pop(0)


class AgentFailurePipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        self.entry = self.library / "entries" / "entry-1"
        self.entry.mkdir(parents=True)
        self.original_gateway = teacher_console_server.AGENT_GATEWAY

    def tearDown(self):
        teacher_console_server.AGENT_GATEWAY = self.original_gateway
        self.temp.cleanup()

    def test_server_gateway_path_uses_single_corrective_retry(self):
        teacher_console_server.AGENT_GATEWAY = FakeGateway([
            {
                "status": "failed",
                "failure_type": "candidate_validation_failed",
                "validation_errors": ["missing student layer"],
                "attempts": [{"provider": "fake", "status": "failed"}],
            },
            {
                "status": "completed",
                "provider": "fake",
                "attempts": [{"provider": "fake", "status": "completed"}],
            },
        ])
        task = {
            "schema_version": 1,
            "id": "task-1",
            "kind": "analysis.generate",
            "entry_dir": str(self.entry),
            "prompt": "generate",
            "allowed_paths": ["solution.md"],
            "input_paths": ["problem.md"],
        }

        result = teacher_console_server.run_agent_gateway(self.entry, task, None)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["failure_repair"]["status"], "recovered")
        self.assertEqual(result["failure_repair"]["retry_count"], 1)
        self.assertEqual(len(teacher_console_server.AGENT_GATEWAY.tasks), 2)
        self.assertIn(
            ".agent-context/failure-evidence.json",
            teacher_console_server.AGENT_GATEWAY.tasks[1]["context_payloads"],
        )

    def test_archive_keeps_failure_type_but_drops_raw_process_output(self):
        gateway = {
            "status": "failed",
            "provider": "claude",
            "failure_type": "provider_timeout",
            "message": "selected provider timed out",
            "stderr": f"secret process output from {self.entry}",
            "attempts": [
                {
                    "provider": "claude",
                    "status": "failed",
                    "failure_type": "provider_timeout",
                    "duration_seconds": 600.0,
                    "error": f"private path {self.entry}",
                }
            ],
            "changed_files": [],
            "validation_errors": [],
            "unauthorized_changes": [],
            "failure_repair": {"status": "not-retried", "retry_count": 0},
        }
        event = teacher_console_server.archive_agent_result(
            self.entry,
            "analysis.generate",
            {"routing_tier": "auto", "model_id": "model-1", "instruction": "private prompt"},
            gateway,
            summary="Agent 生成分层解析候选",
        )

        self.assertEqual(event["result"]["failure_type"], "provider_timeout")
        encoded = json.dumps(event, ensure_ascii=False)
        self.assertNotIn("secret process output", encoded)
        self.assertNotIn("private prompt", encoded)
        self.assertNotIn(str(self.entry), encoded)
        self.assertEqual(
            candidate_archive.read_library_events(self.library)[-1]["event_id"],
            event["event_id"],
        )

    def test_analysis_task_uses_structured_contract_and_minimal_context(self):
        (self.entry / "record.json").write_text(
            '{"schema_version":1,"id":"entry-1","source":{"stored_files":[]}}\n',
            encoding="utf-8",
        )
        (self.entry / "physics-model.json").write_text(
            '{"student_solution":{"quick_answers":["A 错；B 正确"]}}\n',
            encoding="utf-8",
        )
        task = teacher_console_server.analysis_task(
            self.entry,
            "生成解析",
            routing_tier="expert",
            model_config={"provider": "claude", "model": "expert-model"},
        )

        self.assertEqual(task["output_contract"]["name"], "wuli.analysis.v2")
        schema = task["output_contract"]["schema"]
        self.assertNotIn("allOf", schema)
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(
            set(schema["properties"]["metadata"]["required"]),
            set(schema["properties"]["metadata"]["properties"]),
        )
        self.assertEqual(schema["properties"]["diagram"]["properties"], {})
        self.assertEqual(
            set(schema["properties"]["method_check"]["required"]),
            set(schema["properties"]["method_check"]["properties"]),
        )
        self.assertEqual(
            task["structured_context_paths"],
            [
                "problem.md",
                "record.json",
                "physics-model.json",
                ".agent-context/answer-template.md",
                ".agent-context/secondary-conclusions.json",
                ".agent-context/knowledge-evidence.json",
            ],
        )
        evidence = task["context_payloads"][".agent-context/knowledge-evidence.json"]
        self.assertEqual(evidence["task_type"], "analysis.generate")
        self.assertNotIn(".agent-context/library-skill.md", task["context_files"])
        self.assertNotIn("assets/explanatory.svg", task["allowed_paths"])

    def test_w3_claim_verifier_checkpoint_replays_before_gateway(self):
        context = {
            "verification_view": {
                "schema_version": 1,
                "source_facts": [],
                "requests": [],
            }
        }
        digest = teacher_console_server.w3_stage_checkpoint_digest(
            stage="claim-verifier",
            problem="approved problem",
            context=context,
            contract_name="wuli.claim-verify.v1",
            model_id="verifier-model",
            routing_tier="expert",
        )
        same_digest = teacher_console_server.w3_stage_checkpoint_digest(
            stage="claim-verifier",
            problem="approved problem",
            context=context,
            contract_name="wuli.claim-verify.v1",
            model_id="verifier-model",
            routing_tier="expert",
        )
        self.assertEqual(digest, same_digest)
        checkpoint = self.entry / ".cache" / "w3-shadow" / f"claim-verifier-{digest}.json"
        teacher_console_server.kb.write_json(
            checkpoint,
            {
                "schema_version": 1,
                "status": "completed",
                "stage": "claim-verifier",
                "contract": "wuli.claim-verify.v1",
                "payload": {"status": "completed", "claim_audits": []},
                "runtime_identity": {
                    "model_id": "verifier-model",
                    "provider": "fake",
                    "context_isolated": True,
                },
            },
        )
        normalizer_calls = []

        def normalizer(payload):
            normalizer_calls.append(payload)
            return payload

        replayed = teacher_console_server.replay_w3_stage_checkpoint(
            checkpoint,
            normalizer,
            include_runtime_identity=True,
        )
        self.assertEqual(len(normalizer_calls), 1)
        self.assertEqual(replayed["status"], "completed")
        self.assertEqual(replayed["_runtime_identity"]["model_id"], "verifier-model")
        self.assertNotIn("usage", replayed)

    def test_w3_stage_timing_separates_provider_time_from_overhead(self):
        timing = teacher_console_server.summarize_w3_stage_timing(
            {
                "attempts": [
                    {"duration_seconds": 1.25},
                    {"duration_seconds": "2.5"},
                    {"duration_seconds": "invalid"},
                    "invalid",
                ]
            },
            elapsed_seconds=4.5,
        )

        self.assertEqual(timing["duration_seconds"], 4.5)
        self.assertEqual(timing["provider_seconds"], 3.75)
        self.assertEqual(timing["overhead_seconds"], 0.75)
        self.assertEqual(timing["attempt_count"], 3)

    def test_claim_evidence_archive_contains_only_compact_private_telemetry(self):
        request = {
            "status": "completed",
            "routing_tier": "expert",
            "model_id": "verifier-model",
            "report": {
                "claim_evidence_shadow": {
                    "status": "completed",
                    "ledger": {
                        "claims": [
                            {
                                "statement": "private final answer",
                                "local_path": str(self.entry / "answer.md"),
                            }
                        ]
                    },
                    "certificates": [{"decisive_checks": ["private reasoning chain"]}],
                    "aggregation": {"status": "PROVISIONAL"},
                    "metrics": {
                        "claim_count": 4,
                        "certificate_count": 3,
                        "verified_claim_count": 2,
                        "critical_certificate_coverage": 0.5,
                        "unresolved_claim_count": 2,
                    },
                }
            },
        }
        event = teacher_console_server.archive_claim_evidence_shadow(self.entry, request)
        encoded = json.dumps(event, ensure_ascii=False)
        self.assertNotIn("private final answer", encoded)
        self.assertNotIn("private reasoning chain", encoded)
        self.assertNotIn(str(self.entry), encoded)
        self.assertEqual(event["result"]["aggregation_status"], "PROVISIONAL")
        self.assertFalse(event["result"]["canonical_answer_changed"])


if __name__ == "__main__":
    unittest.main()
