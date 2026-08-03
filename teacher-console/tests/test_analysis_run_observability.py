"""Unit tests for truncation diagnosis, failure envelopes, and budget policy
(Wave B of analysis-run-observability; A1 fixture-driven)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
CONSOLE = ROOT / "teacher-console"
for path in (SCRIPTS, CONSOLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_gateway import (  # noqa: E402
    AgentGateway,
    _aggregate_usage,
    _parse_failure_envelope,
    classify_agent_failure,
)
from providers.openai_compatible_agent_adapter import (  # noqa: E402
    DEFAULT_MAX_OUTPUT_TOKENS,
    MAX_OUTPUT_TOKENS_COMPLEX,
    _AdapterFailure,
    _emit_failure_envelope,
    request_options,
    task_is_complex,
)

FIXTURE = ROOT / "teacher-console" / "tests" / "fixtures" / "analysis-run" / "reasoning-only-length.json"


def _load_fixture() -> dict:
    return cast(dict, json.loads(FIXTURE.read_text(encoding="utf-8")))


class TruncationDiagnosisTest(unittest.TestCase):
    def test_reasoning_only_length_fixture_classifies_output_truncated(self):
        fixture = _load_fixture()
        result = {
            "status": "failed",
            "requires_change": True,
            "changed_files": [],
            "stderr": fixture["adapter_stderr"],
            "attempts": [],
        }
        self.assertEqual(classify_agent_failure(result), "output_truncated")
        self.assertNotEqual(classify_agent_failure(result), "candidate_no_change")

    def test_structured_envelope_classifies_output_truncated(self):
        result = {
            "status": "failed",
            "attempts": [
                {
                    "provider": "openai-compatible",
                    "finish_reason": "length",
                    "content_chars": 0,
                    "reasoning_chars": 18547,
                }
            ],
            "requires_change": True,
            "changed_files": [],
        }
        self.assertEqual(classify_agent_failure(result), "output_truncated")

    def test_genuine_no_change_success_still_classifies_candidate_no_change(self):
        result = {
            "status": "failed",
            "requires_change": True,
            "changed_files": [],
            "message": "provider exited without writing files",
            "attempts": [],
        }
        self.assertEqual(classify_agent_failure(result), "candidate_no_change")


class FailureEnvelopeTest(unittest.TestCase):
    def test_parse_envelope_extracts_redacted_telemetry(self):
        stderr = (
            "OpenAI-compatible Agent adapter failed: reached max_tokens before JSON completed\n"
            'WULI_AGENT_FAILURE_ENVELOPE: {"failure_type": "output_truncated", "finish_reason": "length", '
            '"usage": {"completion_tokens": 6000}, "content_chars": 0, "reasoning_chars": 18547, '
            '"request_count": 1, "message": "reached max_tokens"}'
        )
        envelope = _parse_failure_envelope(stderr)
        assert envelope is not None
        self.assertEqual(envelope["finish_reason"], "length")
        self.assertEqual(envelope["content_chars"], 0)
        self.assertEqual(envelope["reasoning_chars"], 18547)
        self.assertEqual(envelope["usage"]["completion_tokens"], 6000)
        self.assertEqual(envelope["failure_type"], "output_truncated")

    def test_parse_envelope_ignores_unmarked_stderr(self):
        self.assertIsNone(_parse_failure_envelope("some random stderr without marker"))
        self.assertIsNone(_parse_failure_envelope(cast(str, None)))

    def test_envelope_never_contains_reasoning_body_or_keys(self):
        with tempfile.TemporaryDirectory() as _:
            import contextlib
            import io

            err = io.StringIO()
            exc = _AdapterFailure(
                "output_truncated",
                message="reached max_tokens",
                usage={"completion_tokens": 6000},
                finish_reason="length",
                content_chars=0,
                reasoning_chars=18547,
            )
            with contextlib.redirect_stderr(err):
                _emit_failure_envelope(exc)
            blob = err.getvalue()
            self.assertIn("WULI_AGENT_FAILURE_ENVELOPE:", blob)
            for forbidden in ("sk-", "api_key", "reasoning_content", "Authorization"):
                self.assertNotIn(forbidden, blob)

    def test_aggregate_usage_sums_attempts(self):
        attempts = [
            {"token_usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            {"token_usage": {"completion_tokens": 6000}},
            {"other": "ignored"},
        ]
        total = _aggregate_usage(attempts)
        self.assertEqual(total["completion_tokens"], 6050)
        self.assertEqual(total["total_tokens"], 6150)


class BudgetPolicyTest(unittest.TestCase):
    def test_gateway_redaction_preserves_only_complexity_summary(self):
        task = {
            "context_payloads": {
                ".agent-context/target-brief.json": {
                    "targets": [{"id": f"Q{index}", "prompt_hint": "private"} for index in range(6)]
                },
                ".agent-context/knowledge-evidence.json": {
                    "references": [{"title": "private evidence"}],
                    "context_budget": {"truncated": True},
                },
            },
            "output_contract": {"schema": {"type": "object"}},
        }
        safe = AgentGateway._task_without_secrets(task)
        self.assertNotIn("context_payloads", safe)
        self.assertEqual(safe["request_complexity"]["target_count"], 6)
        self.assertTrue(safe["request_complexity"]["evidence_truncated"])
        self.assertNotIn("private", json.dumps(safe, ensure_ascii=False))
        self.assertTrue(task_is_complex(safe))
        options = request_options("http://127.0.0.1:1/v1", "m", {}, complex_task=task_is_complex(safe))
        self.assertEqual(options["max_tokens"], MAX_OUTPUT_TOKENS_COMPLEX)
        self.assertEqual(options["thinking"], {"type": "disabled"})

    def test_complex_task_gets_wide_budget_and_thinking_disabled(self):
        task = {"context_payloads": {".agent-context/target-brief.json": {"targets": [{"id": "t1"}] * 6}}}
        self.assertTrue(task_is_complex(task))
        options = request_options("http://127.0.0.1:1/v1", "m", complex_task=True)
        self.assertEqual(options["max_tokens"], MAX_OUTPUT_TOKENS_COMPLEX)
        self.assertEqual(options["thinking"], {"type": "disabled"})

    def test_complex_detection_via_truncated_evidence(self):
        task = {"context_payloads": {".agent-context/knowledge-evidence.json": {"truncated": True}}}
        self.assertTrue(task_is_complex(task))

    def test_complex_detection_via_nested_context_budget(self):
        task = {"context_payloads": {".agent-context/knowledge-evidence.json": {"context_budget": {"truncated": True}}}}
        self.assertTrue(task_is_complex(task))

    def test_simple_task_keeps_env_budget_and_thinking(self):
        task = {"output_contract": {"schema": {"type": "object"}}}
        self.assertFalse(task_is_complex(task))
        options = request_options(
            "http://127.0.0.1:1/v1",
            "m",
            {"TEACHER_CONSOLE_AGENT_API_MAX_OUTPUT_TOKENS": "4000"},
            complex_task=False,
        )
        self.assertEqual(options["max_tokens"], 4000)
        self.assertNotIn("thinking", options)

    def test_complex_raises_default_budget_to_30000(self):
        options = request_options("http://127.0.0.1:1/v1", "m", {}, complex_task=True)
        self.assertEqual(options["max_tokens"], MAX_OUTPUT_TOKENS_COMPLEX)
        self.assertGreater(MAX_OUTPUT_TOKENS_COMPLEX, DEFAULT_MAX_OUTPUT_TOKENS)


if __name__ == "__main__":
    unittest.main()
