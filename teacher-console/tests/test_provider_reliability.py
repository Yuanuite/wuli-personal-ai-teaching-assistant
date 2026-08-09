"""Unit tests for provider reliability: A3.2 stage progress + soft-timeout
envelope (work-tree A2.4/A3.2/A5.2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
for path in (CONSOLE, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from providers.openai_compatible_agent_adapter import (  # noqa: E402
    _AdapterFailure,
    _emit_failure_envelope,
    _urlerror_timeout_signature,
    budgeted_call_timeout,
    call_chat_completion,
)

ADAPTER = CONSOLE / "providers" / "openai_compatible_agent_adapter.py"


def _ok_response() -> bytes:
    payload = {
        "model": "mock",
        "choices": [
            {"finish_reason": "stop", "message": {"content": '{"status":"completed","message":"ok","files":{}}'}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
    return json.dumps(payload).encode("utf-8")


class _OkHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = _ok_response()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: A003
        pass


class _HangHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        time.sleep(30)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # noqa: A003
        pass


class _TrickleHandler(BaseHTTPRequestHandler):
    """Sends one byte per second forever: every socket recv finishes inside
    the HTTP soft deadline, so only the total wall-clock watchdog can stop it."""

    def do_POST(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        for _ in range(30):
            try:
                self.wfile.write(b".")
                self.wfile.flush()
            except OSError:
                return
            time.sleep(1)

    def log_message(self, *args):  # noqa: A003
        pass


def _serve(handler_cls) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


def _run_adapter(
    base_url: str, *, task_extra: dict | None = None, env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    task = {
        "schema_version": 1,
        "id": "test",
        "kind": "analysis.generate",
        "entry_id": "e1",
        "entry_dir": "/tmp",
        "working_dir": "/tmp",
        "prompt": "solve",
        "allowed_paths": [],
        "input_paths": [],
        "output_contract": {"schema": {"type": "object"}, "instructions": ""},
        "timeout_seconds": 90,
        "deadline_budget": {
            "task_deadline": 90,
            "attempt_deadline": 86,
            "http_soft_deadline": 12,
            "cleanup_grace": 2,
        },
    }
    if task_extra:
        task.update(task_extra)
    env = {
        **os.environ,
        "TEACHER_CONSOLE_AGENT_API_BASE_URL": base_url,
        "TEACHER_CONSOLE_AGENT_API_MODEL": "mock",
        "TEACHER_CONSOLE_AGENT_API_KEY": "mock-key",
        "TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS": "300",
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-B", str(ADAPTER)],
        input=json.dumps(task),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class StageProgressTest(unittest.TestCase):
    def test_single_phase_success_reports_stage_progress(self):
        server, base = _serve(_OkHandler)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        result = _run_adapter(base, env_extra={"TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS": "12"})
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        stages = payload["stage_progress"]
        self.assertEqual(len(stages), 1)
        stage = stages[0]
        self.assertEqual(stage["phase"], "single")
        self.assertEqual(stage["finish_reason"], "stop")
        self.assertEqual(stage["usage"]["completion_tokens"], 20)
        self.assertGreater(stage["duration_seconds"], 0)
        self.assertLessEqual(stage["remaining_deadline_seconds"], 90)

    def test_soft_timeout_emits_structured_envelope_with_phase(self):
        server, base = _serve(_HangHandler)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        start = time.monotonic()
        result = _run_adapter(base, env_extra={"TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS": "12"})
        elapsed = time.monotonic() - start
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 20, "must time out at the HTTP soft deadline, not hang")
        self.assertIn("WULI_AGENT_FAILURE_ENVELOPE:", result.stderr)
        envelope_line = next(line for line in result.stderr.splitlines() if "WULI_AGENT_FAILURE_ENVELOPE:" in line)
        envelope = json.loads(envelope_line.split("WULI_AGENT_FAILURE_ENVELOPE:", 1)[1])
        self.assertEqual(envelope["failure_type"], "provider_timeout")
        self.assertEqual(envelope["finish_reason"], "timeout")
        self.assertEqual(envelope["phase"], "single")
        self.assertEqual(envelope["timeout_layer"], "http_soft")
        for forbidden in ("sk-", "reasoning_content", "Authorization"):
            self.assertNotIn(forbidden, envelope_line.lower())

    def test_watchdog_emits_envelope_when_trickle_defeats_soft_deadline(self):
        # Regression (2026-08-04 incident, repeated hard kills with zero
        # output): a trickle stream keeps each recv inside the HTTP timeout,
        # so the soft deadline never fires. The total wall-clock watchdog
        # must emit the structured envelope before the Gateway hard kill.
        server, base = _serve(_TrickleHandler)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        start = time.monotonic()
        result = _run_adapter(
            base,
            task_extra={
                "deadline_budget": {
                    "task_deadline": 6,
                    "attempt_deadline": 6,
                    "http_soft_deadline": 4,
                    "cleanup_grace": 2,
                }
            },
            # Deliberately larger than the budget: simulates a mis-bound or
            # per-recv-only timeout that the watchdog must backstop.
            env_extra={"TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS": "60"},
        )
        elapsed = time.monotonic() - start
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 15, "watchdog must fire before the hard deadline, not hang")
        envelope_line = next(line for line in result.stderr.splitlines() if "WULI_AGENT_FAILURE_ENVELOPE:" in line)
        envelope = json.loads(envelope_line.split("WULI_AGENT_FAILURE_ENVELOPE:", 1)[1])
        self.assertEqual(envelope["failure_type"], "provider_timeout")
        self.assertEqual(envelope["finish_reason"], "timeout")
        self.assertEqual(envelope["timeout_layer"], "task_budget")
        self.assertIn("watchdog", envelope["message"])

class UrlerrorTimeoutSignatureTest(unittest.TestCase):
    """A4.2 (w3-w3r work-tree): URLError-wrapped timeouts classify uniformly."""

    def test_timeout_error_instance_is_timeout(self):
        import socket

        self.assertTrue(_urlerror_timeout_signature(TimeoutError("timed out")))
        self.assertTrue(_urlerror_timeout_signature(socket.timeout("timed out")))

    def test_timeout_message_is_timeout(self):
        self.assertTrue(_urlerror_timeout_signature("timed out"))
        self.assertTrue(_urlerror_timeout_signature("read timeout"))
        self.assertTrue(_urlerror_timeout_signature("[Errno 60] Operation timed out"))

    def test_other_urlerror_reasons_are_not_timeout(self):
        self.assertFalse(_urlerror_timeout_signature("Connection refused"))
        self.assertFalse(_urlerror_timeout_signature("[Errno 61] Connection refused"))
        self.assertFalse(_urlerror_timeout_signature("404 Not Found"))

    def test_refused_connection_classifies_execution_failed_not_timeout(self):
        # A refused connection is NOT a timeout: the adapter must keep the
        # provider_execution_failed classification (no over-classification).
        result = _run_adapter("http://127.0.0.1:1/v1", env_extra={"TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS": "3"})
        self.assertNotEqual(result.returncode, 0)
        envelope_line = next(line for line in result.stderr.splitlines() if "WULI_AGENT_FAILURE_ENVELOPE:" in line)
        envelope = json.loads(envelope_line.split("WULI_AGENT_FAILURE_ENVELOPE:", 1)[1])
        self.assertEqual(envelope["failure_type"], "provider_execution_failed")
        self.assertEqual(envelope["timeout_layer"], "")


class BudgetedCallTimeoutTest(unittest.TestCase):
    """Regression (2026-08-04 incident): the compact contract issues two
    sequential HTTP calls. Each fixed per-call timeout must shrink to the
    remaining attempt budget, otherwise a slow first call plus any second
    call can run past the attempt hard deadline and get killed without a
    structured envelope."""

    def test_second_call_shrinks_to_remaining_attempt_budget(self):
        # Production shape: attempt 90s, soft 76.5s, grace 2s; the core call
        # consumed most of the budget, so the interface call must be capped.
        capped = budgeted_call_timeout(
            76.5, elapsed_seconds=75.0, horizon_seconds=90.0, cleanup_grace_seconds=2.0
        )
        self.assertAlmostEqual(capped, 13.0, places=6)
        self.assertLess(capped, 76.5)

    def test_fresh_budget_keeps_configured_timeout(self):
        capped = budgeted_call_timeout(
            76.5, elapsed_seconds=0.5, horizon_seconds=90.0, cleanup_grace_seconds=2.0
        )
        self.assertAlmostEqual(capped, 76.5, places=6)

    def test_no_horizon_keeps_configured_timeout(self):
        capped = budgeted_call_timeout(
            300, elapsed_seconds=50.0, horizon_seconds=0.0, cleanup_grace_seconds=2.0
        )
        self.assertAlmostEqual(capped, 300.0, places=6)

    def test_exhausted_budget_signals_non_positive_timeout(self):
        capped = budgeted_call_timeout(
            76.5, elapsed_seconds=89.0, horizon_seconds=90.0, cleanup_grace_seconds=2.0
        )
        self.assertLessEqual(capped, 0)

    def test_two_calls_can_never_exceed_hard_deadline(self):
        # Worst case: the first call consumes its full soft deadline.
        horizon, soft, grace = 90.0, 76.5, 2.0
        second = budgeted_call_timeout(
            soft, elapsed_seconds=soft, horizon_seconds=horizon, cleanup_grace_seconds=grace
        )
        self.assertLessEqual(soft + second + grace, horizon)


class AdapterUnitTest(unittest.TestCase):
    def test_call_times_out_with_structured_failure(self):
        from unittest import mock

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            with self.assertRaises(_AdapterFailure) as ctx:
                call_chat_completion(
                    base="http://127.0.0.1:1/v1",
                    model="m",
                    instruction="i",
                    api_key="k",
                    timeout=1,
                    options={"temperature": 0},
                )
        self.assertEqual(ctx.exception.failure_type, "provider_timeout")
        self.assertEqual(ctx.exception.finish_reason, "timeout")

    def test_envelope_carries_phase_and_stage_progress_without_body(self):
        import contextlib
        import io

        err = io.StringIO()
        exc = _AdapterFailure(
            "output_truncated",
            message="truncated",
            finish_reason="length",
            phase="compact-interface",
            stage_progress=[{"phase": "compact-core", "finish_reason": "stop"}],
        )
        with contextlib.redirect_stderr(err):
            _emit_failure_envelope(exc)
        blob = err.getvalue()
        self.assertIn('"phase": "compact-interface"', blob)
        self.assertIn('"stage_progress"', blob)
        self.assertIn("compact-core", blob)
        for forbidden in ("reasoning_content", "sk-", "Authorization"):
            self.assertNotIn(forbidden, blob.lower())


if __name__ == "__main__":
    unittest.main()
