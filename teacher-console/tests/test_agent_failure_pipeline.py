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
            "attempts": [{
                "provider": "claude",
                "status": "failed",
                "failure_type": "provider_timeout",
                "duration_seconds": 600.0,
                "error": f"private path {self.entry}",
            }],
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
        task = teacher_console_server.analysis_task(
            self.entry,
            "生成解析",
            routing_tier="expert",
            model_config={"provider": "claude", "model": "expert-model"},
        )

        self.assertEqual(task["output_contract"]["name"], "wuli.analysis.v1")
        self.assertEqual(
            task["structured_context_paths"],
            [
                "problem.md",
                "record.json",
                ".agent-context/answer-template.md",
                ".agent-context/secondary-conclusions.json",
            ],
        )
        self.assertNotIn(".agent-context/library-skill.md", task["context_files"])
        self.assertIn("assets/explanatory.svg", task["allowed_paths"])


if __name__ == "__main__":
    unittest.main()
