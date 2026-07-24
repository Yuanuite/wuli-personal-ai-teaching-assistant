import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

from agent_gateway import AgentGateway, classify_agent_failure  # noqa: E402
from agent_jobs import AgentJobManager  # noqa: E402


class AgentGatewayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.entry = self.root / "library" / "entries" / "entry-1"
        self.entry.mkdir(parents=True)
        (self.entry / "solution.md").write_text("old solution", encoding="utf-8")
        (self.entry / "record.json").write_text('{"protected":true}\n', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def task(self, **updates):
        value = {
            "schema_version": 1,
            "id": "task-1",
            "kind": "answer.revise",
            "entry_id": self.entry.name,
            "entry_dir": str(self.entry),
            "working_dir": str(self.entry),
            "prompt": f"edit {self.entry}",
            "allowed_paths": ["solution.md"],
            "input_paths": ["solution.md", "record.json", "source/**"],
            "denied_paths": ["record.json"],
            "requires_change": True,
            "workspace_root": str(self.root / "workspaces"),
        }
        value.update(updates)
        return value

    @staticmethod
    def which(name):
        return f"/fake/{name}" if name in {"codex", "claude", "adapter"} else None

    def test_current_codex_command_promotes_valid_staged_change(self):
        commands = []

        def runner(command, cwd=None, input=None, **_kwargs):
            commands.append(command)
            self.assertEqual(input, "")
            Path(cwd, "solution.md").write_text("new solution", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        result = gateway.run(self.task())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provider"], "codex")
        self.assertEqual((self.entry / "solution.md").read_text(encoding="utf-8"), "new solution")
        self.assertIn("--ephemeral", commands[0])
        self.assertIn("--ignore-user-config", commands[0])
        self.assertIn("--ignore-rules", commands[0])
        self.assertNotIn("--ask-for-approval", commands[0])
        self.assertNotIn(str(self.entry), " ".join(commands[0]))

    def test_falls_back_only_when_first_provider_changed_nothing(self):
        called = []

        def runner(command, cwd=None, **_kwargs):
            provider = Path(command[0]).name
            called.append(provider)
            if provider == "codex":
                return subprocess.CompletedProcess(command, 2, stdout="", stderr="unsupported option")
            Path(cwd, "solution.md").write_text("claude result", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        result = gateway.run(self.task())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provider"], "claude")
        self.assertEqual(called, ["codex", "claude"])
        self.assertEqual(len(result["attempts"]), 2)
        # Provider cooldown was moved to per-task level in AgentJobManager;
        # providers remain available after a single failure so other entries
        # can still use them.

    def test_unauthorized_candidate_never_reaches_canonical_entry(self):
        original = (self.entry / "record.json").read_bytes()

        def runner(command, cwd=None, **_kwargs):
            Path(cwd, "record.json").write_text('{"protected":false}\n', encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        result = AgentGateway(environ={}, which=self.which, run=runner).run(self.task())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_type"], "unauthorized_change")
        self.assertIn("record.json", result["unauthorized_changes"])
        self.assertEqual((self.entry / "record.json").read_bytes(), original)

    def test_validator_rejection_leaves_canonical_entry_unchanged(self):
        def runner(command, cwd=None, **_kwargs):
            Path(cwd, "solution.md").write_text("invalid candidate", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        result = AgentGateway(environ={}, which=self.which, run=runner).run(
            self.task(),
            lambda _staging, _changed: ["domain validation failed"],
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_type"], "candidate_validation_failed")
        self.assertEqual(result["validation_errors"], ["domain validation failed"])
        self.assertEqual((self.entry / "solution.md").read_text(encoding="utf-8"), "old solution")

    def test_failure_classifier_prefers_actionable_root_causes(self):
        self.assertEqual(
            classify_agent_failure({
                "status": "failed",
                "unauthorized_changes": ["canonical:solution.md"],
            }),
            "canonical_changed",
        )
        self.assertEqual(
            classify_agent_failure({
                "status": "failed",
                "validation_errors": ["response was truncated before closing JSON"],
            }),
            "output_truncated",
        )
        self.assertEqual(
            classify_agent_failure({
                "status": "failed",
                "returncode": 1,
                "stderr": "HTTP 429 rate limit exceeded",
            }),
            "provider_rate_limited",
        )
        self.assertEqual(
            classify_agent_failure({
                "status": "failed",
                "returncode": 1,
                "stdout": "Error: Exceeded USD budget (0.5)",
                "requires_change": True,
                "changed_files": [],
            }),
            "provider_budget_exceeded",
        )
        self.assertEqual(
            classify_agent_failure({
                "status": "failed",
                "requires_change": True,
                "changed_files": [],
            }),
            "candidate_no_change",
        )

    def test_timeout_stops_before_costly_provider_failover(self):
        called = []

        def runner(command, **_kwargs):
            called.append(Path(command[0]).name)
            raise subprocess.TimeoutExpired(command, 30)

        result = AgentGateway(environ={}, which=self.which, run=runner).run(self.task())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_type"], "provider_timeout")
        self.assertEqual(called, ["codex"])
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["budget_guard"]["reason"], "provider-timeout-consumed-budget")
        self.assertIn("避免继续消耗推理预算", result["message"])

    def test_explicit_single_provider_timeout_does_not_claim_all_providers_failed(self):
        def runner(command, **_kwargs):
            raise subprocess.TimeoutExpired(command, 30)

        gateway = AgentGateway(
            environ={"TEACHER_CONSOLE_AGENT_PROVIDER": "claude"},
            which=self.which,
            run=runner,
        )
        result = gateway.run(self.task())
        self.assertEqual(result["failure_type"], "provider_timeout")
        self.assertEqual(len(result["attempts"]), 1)
        self.assertIn("选定的 Agent provider", result["message"])
        self.assertNotIn("所有 Agent provider", result["message"])

    def test_reported_token_usage_stops_before_costly_provider_failover(self):
        called = []
        payload = {
            "status": "failed",
            "message": "model returned no candidate",
            "files": [],
            "usage": {"prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000},
        }

        def runner(command, **_kwargs):
            called.append(Path(command[0]).name)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        gateway = AgentGateway(
            environ={"TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND": "adapter"},
            which=self.which,
            run=runner,
        )
        result = gateway.run(self.task())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(called, ["adapter"])
        self.assertEqual(result["budget_guard"]["reason"], "failed-attempt-consumed-material-budget")
        self.assertEqual(result["attempts"][0]["token_usage"]["total_tokens"], 1000)

    def test_later_provider_receives_only_remaining_task_time_budget(self):
        observed_timeouts = []

        def runner(command, cwd=None, timeout=None, **_kwargs):
            observed_timeouts.append(timeout)
            if Path(command[0]).name == "codex":
                return subprocess.CompletedProcess(command, 2, stdout="", stderr="quick startup failure")
            Path(cwd, "solution.md").write_text("claude result", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        monotonic_values = [0, 0, 0, 20, 20, 20, 25]
        with mock.patch("agent_gateway.time.monotonic", side_effect=monotonic_values):
            result = gateway.run(self.task(timeout_seconds=40))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(observed_timeouts, [40, 20])

    def test_json_adapter_returns_structured_file_proposals(self):
        payload = {"status": "completed", "message": "revised", "files": {"solution.md": "adapter result"}}

        def runner(command, cwd=None, input=None, **_kwargs):
            self.assertEqual(json.loads(input)["kind"], "answer.revise")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        gateway = AgentGateway(
            environ={"TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND": "adapter", "TEACHER_CONSOLE_AGENT_PROVIDER": "adapter"},
            which=self.which,
            run=runner,
        )
        result = gateway.run(self.task())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provider"], "adapter")
        self.assertEqual((self.entry / "solution.md").read_text(encoding="utf-8"), "adapter result")

    def test_structured_claude_output_is_materialized_without_file_tools(self):
        commands = []

        def runner(command, **_kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({
                    "structured_output": {
                        "status": "completed",
                        "message": "ok",
                        "answer": "structured answer",
                    },
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                }),
                stderr="",
            )

        def materializer(staging, payload):
            Path(staging, "solution.md").write_text(payload["answer"], encoding="utf-8")
            return {"contract": "test.v1", "stages": [{"name": "materialize", "status": "completed"}]}

        task = self.task(
            structured_context_paths=["record.json"],
            output_contract={
                "name": "test.v1",
                "instructions": "Return answer.",
                "schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "message": {"type": "string"},
                        "answer": {"type": "string"},
                    },
                    "required": ["status", "message", "answer"],
                },
            }
        )
        gateway = AgentGateway(
            environ={"TEACHER_CONSOLE_AGENT_PROVIDER": "claude"},
            which=self.which,
            run=runner,
        )
        result = gateway.run(task, materializer=materializer)

        self.assertEqual(result["status"], "completed")
        self.assertEqual((self.entry / "solution.md").read_text(encoding="utf-8"), "structured answer")
        self.assertIn("--json-schema", commands[0])
        self.assertEqual(commands[0][commands[0].index("--tools") + 1], "")
        self.assertNotIn("old solution", commands[0][-1])
        self.assertEqual(result["usage"]["total_tokens"], 13)

    def test_structured_checkpoint_replay_never_calls_provider(self):
        def runner(*_args, **_kwargs):
            self.fail("provider must not be called during checkpoint replay")

        def materializer(staging, payload):
            Path(staging, "solution.md").write_text(payload["answer"], encoding="utf-8")
            return {"contract": "test.v1"}

        task = self.task(
            output_contract={
                "name": "test.v1",
                "schema": {"type": "object"},
            }
        )
        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        result = gateway.replay_structured(
            task,
            {"status": "completed", "message": "ok", "answer": "from checkpoint"},
            None,
            materializer=materializer,
        )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["resumed_from_checkpoint"])
        self.assertEqual((self.entry / "solution.md").read_text(encoding="utf-8"), "from checkpoint")

    def test_inline_evidence_is_materialized_but_not_duplicated_in_adapter_stdin(self):
        payload = {"status": "completed", "message": "revised", "files": {"solution.md": "adapter result"}}

        def runner(command, cwd=None, input=None, **_kwargs):
            child_task = json.loads(input)
            self.assertNotIn("context_payloads", child_task)
            evidence_path = Path(cwd, ".agent-context", "knowledge-evidence.json")
            self.assertEqual(json.loads(evidence_path.read_text(encoding="utf-8"))["references"][0]["title"], "相似题")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        gateway = AgentGateway(
            environ={"TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND": "adapter", "TEACHER_CONSOLE_AGENT_PROVIDER": "adapter"},
            which=self.which,
            run=runner,
        )
        result = gateway.run(
            self.task(
                context_payloads={
                    ".agent-context/knowledge-evidence.json": {"references": [{"title": "相似题"}]},
                },
                evidence_context={"status": "ready", "reference_count": 1, "task_type": "answer.revise"},
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["evidence_context"]["reference_count"], 1)

    def test_inline_context_cannot_escape_agent_context_directory(self):
        gateway = AgentGateway(
            environ={"TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND": "adapter", "TEACHER_CONSOLE_AGENT_PROVIDER": "adapter"},
            which=self.which,
            run=lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="{}", stderr=""),
        )
        with self.assertRaises(PermissionError):
            gateway.run(self.task(context_payloads={".agent-context/../escaped.json": {"secret": True}}))

    def test_task_model_config_can_select_openai_compatible_provider(self):
        payload = {
            "status": "completed",
            "message": "picked model",
            "model": "picked-model",
            "files": {"solution.md": "openai-compatible result"},
        }

        def runner(command, cwd=None, input=None, env=None, **_kwargs):
            self.assertIn("openai_compatible_agent_adapter.py", command[-1])
            self.assertEqual(env["TEACHER_CONSOLE_AGENT_API_BASE_URL"], "http://127.0.0.1:9000/v1")
            self.assertEqual(env["TEACHER_CONSOLE_AGENT_API_MODEL"], "picked-model")
            self.assertEqual(json.loads(input)["model_config"]["id"], "picked")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        result = gateway.run(
            self.task(
                allow_remote=True,
                model_config={
                    "id": "picked",
                    "display_name": "教师选择模型",
                    "provider": "openai-compatible",
                    "base_url": "http://127.0.0.1:9000/v1",
                    "model": "picked-model",
                    "api_key": "picked-key",
                    "model_tier": "custom",
                }
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["provider"], "openai-compatible")
        self.assertEqual(result["model_id"], "picked")
        self.assertEqual(result["model_display_name"], "教师选择模型")
        self.assertEqual((self.entry / "solution.md").read_text(encoding="utf-8"), "openai-compatible result")

    def test_direct_model_api_key_is_environment_only_not_task_stdin(self):
        payload = {
            "status": "completed",
            "message": "picked model",
            "files": {"solution.md": "openai-compatible result"},
        }

        def runner(command, cwd=None, input=None, env=None, **_kwargs):
            self.assertEqual(env["TEACHER_CONSOLE_AGENT_API_KEY"], "picked-key")
            self.assertNotIn("picked-key", input)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        result = gateway.run(
            self.task(
                model_config={
                    "id": "picked",
                    "display_name": "教师选择模型",
                    "provider": "openai-compatible",
                    "base_url": "http://127.0.0.1:9000/v1",
                    "model": "picked-model",
                    "api_key": "picked-key",
                }
            )
        )
        self.assertEqual(result["status"], "completed")

    def test_claude_model_config_can_override_attempt_timeout(self):
        observed = {}

        def runner(command, cwd=None, timeout=None, **_kwargs):
            observed["timeout"] = timeout
            observed["command"] = command
            Path(cwd, "solution.md").write_text("claude result", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        result = gateway.run(
            self.task(
                model_config={
                    "id": "slow-analysis",
                    "provider": "claude",
                    "model": "slow-analysis",
                    "timeout_seconds": "900",
                }
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(observed["timeout"], 900)
        self.assertIn("--max-budget-usd", observed["command"])
        self.assertIn("0.50", observed["command"])
        self.assertIn("Read,Write,Edit,Glob,Grep", observed["command"])

    def test_claude_model_config_isolates_backend_and_credentials(self):
        observed = {}

        def runner(command, cwd=None, env=None, input=None, **_kwargs):
            observed["command"] = command
            observed["env"] = env
            self.assertEqual(input, "")
            Path(cwd, "solution.md").write_text("claude-compatible result", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        gateway = AgentGateway(
            environ={
                "ANTHROPIC_BASE_URL": "https://stale.example.test",
                "ANTHROPIC_AUTH_TOKEN": "stale-token",
            },
            which=self.which,
            run=runner,
        )
        result = gateway.run(
            self.task(
                allow_remote=True,
                model_config={
                    "id": "deepseek-agent",
                    "provider": "claude",
                    "base_url": "https://api.example.test",
                    "model": "deepseek-agent-model",
                    "api_key": "selected-token",
                }
            )
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(observed["env"]["ANTHROPIC_BASE_URL"], "https://api.example.test")
        self.assertEqual(observed["env"]["ANTHROPIC_AUTH_TOKEN"], "selected-token")
        self.assertEqual(observed["env"]["ANTHROPIC_API_KEY"], "selected-token")
        self.assertIn("deepseek-agent-model", observed["command"])
        self.assertNotIn("selected-token", " ".join(observed["command"]))

    def test_live_probe_quarantines_unresponsive_provider_without_student_data(self):
        seen = []

        def runner(command, cwd=None, input=None, **_kwargs):
            seen.append({"command": command, "cwd": cwd, "input": input})
            if "--version" in command:
                return subprocess.CompletedProcess(command, 0, stdout="test-cli 1.0", stderr="")
            if "--help" in command:
                flags = (
                    "--sandbox --ephemeral --cd --ignore-user-config --print "
                    "--permission-mode --no-session-persistence --safe-mode"
                )
                return subprocess.CompletedProcess(command, 0, stdout=flags, stderr="")
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="model unavailable")

        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        health = gateway.probe("codex", timeout_seconds=12)
        self.assertEqual(health["live_probe"]["status"], "failed")
        self.assertFalse(health["live_probe"]["student_data_sent"])
        probe_call = next(item for item in seen if item["cwd"] is not None)
        self.assertEqual(probe_call["input"], "")
        self.assertNotIn(str(self.entry), " ".join(probe_call["command"]))
        self.assertIn("read-only", probe_call["command"])
        # Probe failure is reported in live_probe, but no longer marks the
        # provider as unavailable — cooldown moved to per-task level.

    def test_live_file_probe_requires_exact_isolated_write(self):
        seen = []

        def runner(command, cwd=None, input=None, **_kwargs):
            seen.append({"command": command, "cwd": cwd, "input": input})
            if "--version" in command:
                return subprocess.CompletedProcess(command, 0, stdout="test-cli 1.0", stderr="")
            if "--help" in command:
                flags = (
                    "--sandbox --ephemeral --cd --ignore-user-config --print "
                    "--permission-mode --no-session-persistence --safe-mode"
                )
                return subprocess.CompletedProcess(command, 0, stdout=flags, stderr="")
            Path(cwd, "gateway-probe.txt").write_text("GATEWAY_PROBE_OK\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        gateway = AgentGateway(environ={}, which=self.which, run=runner)
        health = gateway.probe("codex", timeout_seconds=12, require_file_tools=True)
        self.assertEqual(health["live_probe"]["status"], "passed")
        self.assertEqual(health["live_probe"]["capability"], "filesystem-write")
        self.assertFalse(health["live_probe"]["student_data_sent"])
        probe_call = next(item for item in seen if item["cwd"] is not None)
        self.assertIn("workspace-write", probe_call["command"])
        self.assertNotIn(str(self.entry), " ".join(probe_call["command"]))

    def test_staging_does_not_follow_or_expose_entry_symlinks(self):
        secret = self.root / "outside-secret.txt"
        secret.write_text("must stay outside", encoding="utf-8")
        link = self.entry / "linked-secret.txt"
        try:
            os.symlink(secret, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")

        def runner(command, cwd=None, **_kwargs):
            self.assertFalse(Path(cwd, "linked-secret.txt").exists())
            Path(cwd, "solution.md").write_text("safe result", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        result = AgentGateway(environ={}, which=self.which, run=runner).run(self.task())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(secret.read_text(encoding="utf-8"), "must stay outside")

    def test_hidden_source_images_are_restored_only_for_validation(self):
        source = self.entry / "source" / "original.png"
        source.parent.mkdir()
        source.write_bytes(b"private-image")
        validator_saw_source = []

        def runner(command, cwd=None, **_kwargs):
            self.assertFalse(Path(cwd, "source/original.png").exists())
            Path(cwd, "solution.md").write_text("validated result", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        def validator(staging, _changed):
            validator_saw_source.append((staging / "source/original.png").read_bytes())
            return []

        result = AgentGateway(environ={}, which=self.which, run=runner).run(
            self.task(hidden_paths=["source/original.png"]),
            validator,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(validator_saw_source, [b"private-image"])

    def test_provider_receives_only_declared_input_files(self):
        (self.entry / "pipeline.json").write_text('{"private":true}\n', encoding="utf-8")
        (self.entry / "answer-review.json").write_text('{"reviewer":"teacher"}\n', encoding="utf-8")

        def runner(command, cwd=None, **_kwargs):
            self.assertFalse(Path(cwd, "pipeline.json").exists())
            self.assertFalse(Path(cwd, "answer-review.json").exists())
            self.assertTrue(Path(cwd, "record.json").exists())
            Path(cwd, "solution.md").write_text("allowlisted result", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        result = AgentGateway(environ={}, which=self.which, run=runner).run(self.task())
        self.assertEqual(result["status"], "completed")

    def test_provider_environment_is_minimized(self):
        def runner(command, cwd=None, env=None, **_kwargs):
            self.assertEqual(env["OPENAI_API_KEY"], "provider-key")
            self.assertEqual(env["PATH"], "/safe/bin")
            self.assertNotIn("UNRELATED_CLOUD_SECRET", env)
            Path(cwd, "solution.md").write_text("environment-safe result", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

        gateway = AgentGateway(
            environ={
                "PATH": "/safe/bin",
                "OPENAI_API_KEY": "provider-key",
                "UNRELATED_CLOUD_SECRET": "must-not-leak",
            },
            which=self.which,
            run=runner,
        )
        result = gateway.run(self.task())
        self.assertEqual(result["status"], "completed")


class AgentJobManagerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "jobs"

    def tearDown(self):
        self.temp.cleanup()

    def test_persists_job_and_blocks_same_entry_concurrency(self):
        manager = AgentJobManager(self.directory, max_workers=1)
        started = threading.Event()
        release = threading.Event()

        def work():
            started.set()
            release.wait(2)
            return {"status": "completed", "provider": "fake"}

        first = manager.submit("answer.revise", "entry-1", work)
        self.assertEqual(first["status"], "queued")
        self.assertTrue(started.wait(2))
        second = manager.submit("visualization.model", "entry-1", lambda: {"status": "completed"})
        self.assertEqual(second["status"], "blocked")
        release.set()
        manager.shutdown(wait=True)
        stored = manager.get(first["job"]["id"])
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["result"]["provider"], "fake")

    def test_source_clean_can_run_for_multiple_entries_in_parallel(self):
        manager = AgentJobManager(self.directory, max_workers=4, kind_limits={"source.clean": 2})
        first_started = threading.Event()
        second_started = threading.Event()
        release = threading.Event()

        def work(started):
            started.set()
            release.wait(2)
            return {"status": "completed", "provider": "fake"}

        first = manager.submit("source.clean", "entry-1", lambda: work(first_started))
        second = manager.submit("source.clean", "entry-2", lambda: work(second_started))
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "queued")
        self.assertTrue(first_started.wait(2))
        self.assertTrue(second_started.wait(2))
        release.set()
        manager.shutdown(wait=True)
        self.assertEqual(manager.get(first["job"]["id"])["status"], "completed")
        self.assertEqual(manager.get(second["job"]["id"])["status"], "completed")

    def test_higher_risk_job_kinds_remain_limited(self):
        manager = AgentJobManager(self.directory, max_workers=4, kind_limits={"answer.revise": 1})
        first_started = threading.Event()
        second_started = threading.Event()
        release = threading.Event()

        def work(started):
            started.set()
            release.wait(2)
            return {"status": "completed"}

        first = manager.submit("answer.revise", "entry-1", lambda: work(first_started))
        second = manager.submit("answer.revise", "entry-2", lambda: work(second_started))
        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "queued")
        self.assertTrue(first_started.wait(2))
        self.assertFalse(second_started.wait(0.1))
        release.set()
        manager.shutdown(wait=True)
        self.assertEqual(manager.get(first["job"]["id"])["status"], "completed")
        self.assertEqual(manager.get(second["job"]["id"])["status"], "completed")

    def test_waiting_kind_limited_job_does_not_starve_other_kinds(self):
        manager = AgentJobManager(
            self.directory,
            scheduler_config={
                "global_max_running": 2,
                "kind_limits": {"answer.revise": 1, "source.clean": 1},
                "kind_priorities": {"answer.revise": 80, "source.clean": 70},
            },
        )
        first_revision_started = threading.Event()
        source_clean_started = threading.Event()
        release = threading.Event()

        def revision():
            first_revision_started.set()
            release.wait(2)
            return {"status": "completed"}

        def source_clean():
            source_clean_started.set()
            return {"status": "completed"}

        first = manager.submit("answer.revise", "entry-1", revision)
        blocked_by_kind = manager.submit("answer.revise", "entry-2", lambda: {"status": "completed"})
        clean = manager.submit("source.clean", "entry-3", source_clean)
        self.assertEqual(first["status"], "queued")
        self.assertEqual(blocked_by_kind["status"], "queued")
        self.assertEqual(clean["status"], "queued")
        self.assertTrue(first_revision_started.wait(2))
        self.assertTrue(source_clean_started.wait(2))
        release.set()
        manager.shutdown(wait=True)
        self.assertEqual(manager.get(first["job"]["id"])["status"], "completed")
        self.assertEqual(manager.get(blocked_by_kind["job"]["id"])["status"], "completed")
        self.assertEqual(manager.get(clean["job"]["id"])["status"], "completed")

    def test_restart_marks_running_job_interrupted(self):
        self.directory.mkdir(parents=True)
        path = self.directory / "old-job.json"
        path.write_text(
            json.dumps({
                "schema_version": 1,
                "id": "old-job",
                "kind": "analysis.generate",
                "entry_id": "entry-1",
                "status": "running",
                "created_at": "2026-07-20T00:00:00+08:00",
            }),
            encoding="utf-8",
        )
        manager = AgentJobManager(self.directory)
        recovered = manager.get("old-job")
        manager.shutdown()
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["failure_type"], "worker_interrupted")
        self.assertIn("重启", recovered["error"])

    def test_job_exception_has_structured_failure_type(self):
        def broken():
            raise RuntimeError("boom")

        manager = AgentJobManager(self.directory, max_workers=1)
        submitted = manager.submit("answer.revise", "entry-exception", broken)
        manager.shutdown(wait=True)
        record = manager.get(submitted["job"]["id"])
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["failure_type"], "task_exception")
        self.assertEqual(AgentJobManager.public(record)["failure_type"], "task_exception")

    def test_job_copies_gateway_failure_type_to_top_level_record(self):
        manager = AgentJobManager(self.directory, max_workers=1)
        submitted = manager.submit(
            "analysis.generate",
            "entry-timeout",
            lambda: {
                "status": "failed",
                "message": "selected provider timed out",
                "failure_type": "provider_timeout",
            },
        )
        manager.shutdown(wait=True)
        record = manager.get(submitted["job"]["id"])
        self.assertEqual(record["failure_type"], "provider_timeout")
        self.assertEqual(AgentJobManager.public(record)["failure_type"], "provider_timeout")

    def test_provider_quota_releases_the_provider_reserved_at_start(self):
        manager = AgentJobManager(
            self.directory,
            scheduler_config={
                "global_max_running": 1,
                "provider_limits": {"configured": 1, "fallback": 1},
            },
        )
        submitted = manager.submit(
            "analysis.generate",
            "entry-fallback",
            lambda: {"status": "completed", "provider": "fallback"},
            metadata={"provider": "configured"},
        )
        manager.shutdown(wait=True)

        self.assertEqual(manager.get(submitted["job"]["id"])["provider"], "fallback")
        self.assertEqual(manager.running_by_provider.get("configured"), 0)
        self.assertEqual(manager.running_by_provider.get("fallback", 0), 0)


if __name__ == "__main__":
    unittest.main()
