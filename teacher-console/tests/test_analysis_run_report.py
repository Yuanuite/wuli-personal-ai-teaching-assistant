"""Unit tests for the read-only analysis-run report (Wave A3 schema + Wave D script).

Covers: schema acceptance/rejection, reasoning-only truncation diagnosis
(output_truncated) with audit-preserved recorded failure type, usage recovery
from stderr telemetry, exit codes 0/2/3/4, sensitive-field leak scan, and
counts semantics (provider attempts, checkpoint replays, rollback gating).
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
for path in (CONSOLE,):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

SPEC = importlib.util.spec_from_file_location("analysis_run_report", CONSOLE / "scripts" / "analysis_run_report.py")
assert SPEC is not None
report_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report_mod)

from typing import cast

from route_snapshot import route_config_digest  # noqa: E402

FAILED_JOB_ID = "deadbeefdeadbeefdeadbeefdeadbeef01"
COMPLETED_JOB_ID = "deadbeefdeadbeefdeadbeefdeadbeef02"
ENTRY_ID = "20260802-test-entry-00000001"

FAILED_STDERR = (
    "OpenAI-compatible Agent adapter failed: model response reached max_tokens "
    "before JSON completed; content_chars=0; reasoning_chars=18547; completion_tokens=6000"
)


def _library(tmp: Path) -> Path:
    lib = tmp / "library"
    (lib / ".cache" / "agent-jobs").mkdir(parents=True)
    (lib / "entries").mkdir(parents=True)
    (lib / "config").mkdir(parents=True)
    (lib / "config" / "model-registry.json").write_text(
        json.dumps({"id": "deepseek-v4-flash-api", "api_key": "sk-fake-fixture-only", "provider": "openai-compatible"}),
        encoding="utf-8",
    )
    (lib / "config" / "analysis-production-routing.json").write_text(
        json.dumps({"schema_version": 1, "policy_version": "wuli-core-first-routing-v1", "mode": "core-first"}),
        encoding="utf-8",
    )
    return lib


def _entry(lib: Path, *, answer_review=True, source_review=True, archive=True) -> Path:
    entry = lib / "entries" / ENTRY_ID
    entry.mkdir(parents=True)
    if source_review:
        (entry / "source-review.json").write_text(
            json.dumps({
                "entry_id": ENTRY_ID,
                "input_digest": "0fb6853185b2140e367f528f8430867efc0949cfe73ffdfcdaabfeb992745bc2",
                "problem_sha256": "16667ae0b6db39fede04ec098ae0af44b98e0bf21e1a099b970a241b04c99583",
                "method": "human",
                "reviewer": "teacher",
                "status": "passed",
            }),
            encoding="utf-8",
        )
    if answer_review:
        (entry / "answer-review.json").write_text(
            json.dumps({
                "entry_id": ENTRY_ID,
                "answer_digest": "2188b9572725d888dcabc732b2f184a0c85764f5ec07a987eb21b378d8893e50",
                "reviewed_at": "2026-08-02T18:00:00+08:00",
                "reviewer": "teacher",
                "status": "passed",
            }),
            encoding="utf-8",
        )
    if archive:
        event_id = f"{ENTRY_ID}-archive01"
        (entry / "candidate-archive.jsonl").write_text(
            json.dumps(
                {
                    "actor": "agent",
                    "changed_files": ["solution.md", "student-solution.md", "teacher-solution.md", "record.json"],
                    "created_at": "2026-08-02T18:01:00+08:00",
                    "entry_id": ENTRY_ID,
                    "event_id": event_id,
                    "event_type": "agent-result",
                    "raw_status": "completed",
                    "status": "succeeded",
                    "evaluation": {"status": "succeeded", "scores": {}},
                    "task_type": "analysis.generate",
                    "schema_version": 2,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    return entry


def _failed_job() -> dict:
    return {
        "schema_version": 1,
        "id": FAILED_JOB_ID,
        "kind": "analysis.generate",
        "entry_id": ENTRY_ID,
        "status": "failed",
        "priority": 60,
        "sequence": 2,
        "created_at": "2026-08-02T20:38:00.370215+08:00",
        "routing_tier": "auto",
        "model_id": "deepseek-v4-flash-api",
        "provider": "openai-compatible",
        "started_at": "2026-08-02T20:38:00.373363+08:00",
        "completed_at": "2026-08-02T20:39:01.478770+08:00",
        "failure_type": "candidate_no_change",
        "error": "选定的 Agent provider 在修改文件前失败；为避免重复消耗推理预算，任务已安全停止。\n失败类型：candidate_no_change\nprovider：openai-compatible",  # noqa: E501
        "result": {
            "schema_version": 1,
            "entry_id": ENTRY_ID,
            "status": "failed",
            "model_id": "deepseek-v4-flash-api",
            "model_display_name": "DeepSeek V4 Flash (API)",
            "provider": "openai-compatible",
            "returncode": 1,
            "stdout": "",
            "stderr": FAILED_STDERR,
            "changed_files": [],
            "unauthorized_changes": [],
            "validation_errors": [],
            "attempts": [
                {
                    "provider": "openai-compatible",
                    "status": "failed",
                    "returncode": 1,
                    "stdout": "",
                    "stderr": FAILED_STDERR + "\n",
                    "changed_files": [],
                    "unauthorized_changes": [],
                    "validation_errors": [],
                    "requires_change": True,
                    "started_at": "2026-08-02T20:38:00+08:00",
                    "duration_seconds": 60.969,
                    "failure_type": "candidate_no_change",
                    "budget_guard": "stopped-before-costly-failover",
                }
            ],
            "stages": [
                {
                    "name": "structured-generation",
                    "status": "failed",
                    "provider": "openai-compatible",
                    "duration_seconds": 60.969,
                    "failure_type": "candidate_no_change",
                },
                {"name": "authoritative-review", "status": "not-run", "authority": "teacher-or-standard-answer"},
            ],
            "resulting_state": "needs-analysis-and-answer",
            "failure_type": "candidate_no_change",
            "archive_event_id": f"{ENTRY_ID}-archive01",
            "evidence_context": {
                "status": "ready",
                "reference_count": 3,
                "budget": {"requested_chars": 8000, "serialized_chars": 7586, "truncated": True},
            },
            "budget_guard": {
                "status": "stopped",
                "reason": "failed-attempt-consumed-material-budget",
                "provider": "openai-compatible",
                "threshold_seconds": 30.0,
                "duration_seconds": 60.969,
            },
            "adaptive_routing": {
                "schema_version": 1,
                "policy_version": "wuli-core-first-routing-v1",
                "mode": "core-first",
                "route": "core",
                "selected_route": "core",
                "reason": "unified-core-first-default",
                "config_errors": [],
                "limits": {"max_latency_seconds": 90, "max_agent_calls": 1},
            },
            "target_brief": {
                "schema_version": 1,
                "method_profile": "high_school_standard",
                "digest": "6b85dce801cd9f50f0322bdce41bda092bb1ee3dcd9c36452f65fc2a87f76716",
                "targets": [{"id": "Q1", "prompt_hint": "compute E(r)"}],
                "risk_signals": ["multiple-targets"],
            },
        },
        "outcome": {
            "schema_version": 1,
            "status": "failed",
            "provider": "openai-compatible",
            "model": "deepseek-v4-flash-api",
            "failure_type": "candidate_no_change",
            "usage": {"measurement": "unavailable"},
            "attempts": {"count": 1, "providers": ["openai-compatible"], "provider_seconds": 60.969},
            "timing": {"queue_seconds": 0.003, "run_seconds": 61.105, "total_seconds": 61.109},
            "controls": {
                "budget_guard_reason": "failed-attempt-consumed-material-budget",
                "resumed_from_checkpoint": False,
            },
            "evidence_context": {
                "status": "ready",
                "reference_count": 3,
                "budget": {"requested_chars": 8000, "serialized_chars": 7586, "truncated": True},
            },
        },
    }


def _completed_job(lib: Path, *, snapshot: bool = True) -> dict:
    job = {
        "schema_version": 1,
        "id": COMPLETED_JOB_ID,
        "kind": "analysis.generate",
        "entry_id": ENTRY_ID,
        "status": "completed",
        "created_at": "2026-08-02T19:00:00+08:00",
        "started_at": "2026-08-02T19:00:01+08:00",
        "completed_at": "2026-08-02T19:02:00+08:00",
        "model_id": "Deepseek-v4-pro",
        "provider": "claude",
        "failure_type": "",
        "result": {
            "schema_version": 1,
            "entry_id": ENTRY_ID,
            "status": "completed",
            "model_id": "Deepseek-v4-pro",
            "model_display_name": "dpsk-pro",
            "provider": "claude",
            "returncode": 0,
            "stdout": "",
            "stderr": "",
            "changed_files": [
                "solution.md",
                "student-solution.md",
                "teacher-solution.md",
                "record.json",
                "assets/explanatory.svg",
            ],
            "unauthorized_changes": [],
            "validation_errors": [],
            "attempts": [
                {
                    "provider": "claude",
                    "status": "completed",
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "changed_files": [
                        "solution.md",
                        "student-solution.md",
                        "teacher-solution.md",
                        "record.json",
                        "assets/explanatory.svg",
                    ],
                    "unauthorized_changes": [],
                    "validation_errors": [],
                    "requires_change": True,
                    "started_at": "2026-08-02T19:00:01+08:00",
                    "duration_seconds": 113.389,
                    "failure_type": "",
                }
            ],
            "stages": [
                {
                    "name": "structured-generation",
                    "status": "completed",
                    "provider": "claude",
                    "duration_seconds": 113.389,
                },
                {"name": "authoritative-review", "status": "not-run", "authority": "teacher-or-standard-answer"},
            ],
            "resulting_state": "needs-answer-review",
            "archive_event_id": f"{ENTRY_ID}-archive01",
            "evidence_context": {
                "status": "ready",
                "reference_count": 4,
                "budget": {"requested_chars": 8000, "serialized_chars": 4500, "truncated": False},
            },
            "adaptive_routing": {
                "schema_version": 1,
                "policy_version": "wuli-core-first-routing-v1",
                "mode": "core-first",
                "route": "core",
                "selected_route": "core",
                "reason": "unified-core-first-default",
                "config_errors": [],
                "limits": {"max_latency_seconds": 90, "max_agent_calls": 1},
            },
            "target_brief": {
                "schema_version": 1,
                "method_profile": "high_school_standard",
                "digest": "6b85dce801cd9f50f0322bdce41bda092bb1ee3dcd9c36452f65fc2a87f76716",
                "targets": [{"id": "Q1", "prompt_hint": "find B(r)"}],
                "risk_signals": [],
            },
        },
        "outcome": {
            "schema_version": 1,
            "status": "completed",
            "provider": "claude",
            "model": "Deepseek-v4-pro",
            "failure_type": None,
            "usage": {
                "measurement": "provider-reported",
                "input_tokens": 12613,
                "output_tokens": 12242,
                "total_tokens": 24855,
            },
            "attempts": {"count": 1, "providers": ["claude"], "provider_seconds": 113.389},
            "timing": {"queue_seconds": 0.001, "run_seconds": 113.389, "total_seconds": 113.390},
            "controls": {"budget_guard_reason": None, "resumed_from_checkpoint": False},
            "evidence_context": {
                "status": "ready",
                "reference_count": 4,
                "budget": {"serialized_chars": 4500, "truncated": False},
            },
        },
    }
    if snapshot:
        from route_snapshot import build_route_snapshot

        job["route_snapshot"] = build_route_snapshot(
            kind="analysis.generate",
            routing_tier="auto",
            requested_model_id="Deepseek-v4-pro",
            config={"id": "Deepseek-v4-pro", "provider": "claude", "model": "Deepseek-v4-pro"},
            library=lib,
        )
    return job


def _w3_report() -> dict:
    return {
        "kind": "w3-shadow-analysis",
        "entry_id": ENTRY_ID,
        "status": "completed",
        "model_id": "codex-visualization",
        "routing_tier": "shadow",
        "policy": "wuli-w3-shadow-v1",
        "stages": [
            {
                "stage": "decompose",
                "provider": "checkpoint",
                "status": "completed",
                "model_id": "Deepseek-v4-pro",
                "usage": {},
            },
            {
                "stage": "solver-a",
                "provider": "checkpoint",
                "status": "completed",
                "model_id": "Deepseek-v4-pro",
                "usage": {},
            },
            {
                "stage": "verifier",
                "provider": "checkpoint",
                "status": "completed",
                "model_id": "Deepseek-v4-flash",
                "usage": {},
            },
            {
                "stage": "solver-b",
                "provider": "codex",
                "status": "completed",
                "model_id": "codex-visualization",
                "usage": {},
            },
            {
                "stage": "adjudicator",
                "provider": "codex",
                "status": "completed",
                "model_id": "codex-visualization",
                "usage": {},
            },
        ],
        "report": {
            "verifier": {"status": "completed"},
            "screen": {"decision": "decompose", "score": 6},
            "metrics": {
                "solver_b_used": True,
                "target_count": 4,
                "verified_target_count": 4,
                "conflict_target_count": 2,
            },
            "adjudication": {
                "status": "completed",
                "target_decisions": [{"target_id": "T_C", "decision": "recomputed"}],
            },
            "blueprint": {"verification_obligations": [{"id": "VO_A", "check": "核对穿越次数计数"}]},
            "recommended_student_solution": "## 答案速览\n- 选项C正确。",
        },
    }


def _write_job(lib: Path, job: dict) -> None:
    (lib / ".cache" / "agent-jobs" / f"{job['id']}.json").write_text(
        json.dumps(job, ensure_ascii=False), encoding="utf-8"
    )


def _run_cli(lib: Path, *args: str) -> int:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = report_mod.main(list(args), library=lib)
    return code


class SchemaTest(unittest.TestCase):
    def _valid_report(self) -> dict:
        return {
            "schema": "wuli.analysis-run-report.v1",
            "run": {
                "job_id": "a" * 32,
                "entry_id": ENTRY_ID,
                "status": "failed",
                "mode": "core-first",
                "selected_route": "core",
            },
            "runtime_identities": [{"registered_id": "m1", "provider": "claude", "kind": "model", "source": "job"}],
            "steps": [
                {
                    "step_id": "P00",
                    "name": "job + route snapshot",
                    "category": "inputs",
                    "contract": "wuli.agent-job.v1",
                    "input_fingerprint": "sha256:" + "0" * 64,
                    "runtime_identity": {
                        "kind": "local-verifier",
                        "registered_id": None,
                        "provider": None,
                        "source": "deterministic-gate",
                    },
                    "started_at": "2026-08-02T20:38:00+08:00",
                    "duration": 1.0,
                    "attempt_count": 1,
                    "upstream_request_count": 1,
                    "artifact_refs": ["solution.md"],
                    "verification_obligations": ["x"],
                    "verification_result": "passed",
                    "failure_type": None,
                    "retry_or_backjump": None,
                    "terminal_effect": None,
                }
            ],
            "counts": {
                "logical_stage_count": 1,
                "provider_attempt_count": 1,
                "upstream_request_count": 1,
                "checkpoint_replay_count": 0,
                "rollback_count": 0,
                "supplemental_analysis_count": 0,
                "control_transition_count": 0,
            },
            "verification_summary": {"exit_code": 3},
            "terminal": {
                "status": "failed",
                "diagnosed_failure_type": "output_truncated",
                "recorded_failure_type": "candidate_no_change",
            },
            "redactions": ["api-keys"],
        }

    def test_valid_report_passes_schema(self):
        errors, available = report_mod.validate_schema(self._valid_report())
        self.assertTrue(available)
        self.assertEqual(errors, [])

    def test_unknown_field_fails_schema(self):
        payload = self._valid_report()
        payload["bogus_field"] = 1
        errors, _ = report_mod.validate_schema(payload)
        self.assertTrue(any("bogus_field" in e for e in errors))

    def test_sensitive_field_fails_schema_at_root(self):
        payload = self._valid_report()
        payload["api_key"] = "sk-live-secret"
        errors, _ = report_mod.validate_schema(payload)
        self.assertTrue(any("api_key" in e for e in errors))

    def test_sensitive_field_fails_schema_inside_terminal(self):
        payload = self._valid_report()
        payload["terminal"]["Authorization"] = "Bearer xyz"
        errors, _ = report_mod.validate_schema(payload)
        self.assertTrue(any("Authorization" in e for e in errors))

    def test_sensitive_field_fails_schema_inside_step(self):
        payload = self._valid_report()
        payload["steps"][0]["data:uri"] = "data:image/png;base64,AAAA"
        errors, _ = report_mod.validate_schema(payload)
        self.assertTrue(any("data:uri" in e for e in errors))


class DiagnosisTest(unittest.TestCase):
    def test_reasoning_only_length_is_output_truncated(self):
        diagnosis = report_mod.diagnose_failure(_failed_job())
        self.assertEqual(diagnosis["diagnosed"], "output_truncated")
        self.assertEqual(diagnosis["recorded"], "candidate_no_change")
        self.assertEqual(diagnosis["basis"], "stderr-telemetry")
        self.assertEqual(diagnosis["telemetry"]["finish_reason"], "length")
        self.assertEqual(diagnosis["telemetry"]["content_chars"], 0)
        self.assertEqual(diagnosis["telemetry"]["reasoning_chars"], 18547)

    def test_existing_diagnosed_failure_type_is_preserved(self):
        job = _failed_job()
        job["diagnosed_failure_type"] = "output_truncated"
        diagnosis = report_mod.diagnose_failure(job)
        self.assertEqual(diagnosis["basis"], "job.diagnosed_failure_type")

    def test_genuine_no_change_keeps_recorded_type(self):
        job = _failed_job()
        job["result"]["stderr"] = "provider exited without writing files"
        job["result"]["attempts"][0]["stderr"] = "provider exited without writing files"
        diagnosis = report_mod.diagnose_failure(job)
        self.assertEqual(diagnosis["diagnosed"], "candidate_no_change")
        self.assertEqual(diagnosis["basis"], "recorded-failure_type")


class FailedJobReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = _library(Path(self.tmp.name))
        _entry(self.lib)
        _write_job(self.lib, _failed_job())

    def test_report_fields_and_exit_code_3(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = report_mod.main(["--job-id", FAILED_JOB_ID, "--format", "json"], library=self.lib)
        self.assertEqual(code, 3)
        report = json.loads(out.getvalue())
        self.assertEqual(report["schema"], "wuli.analysis-run-report.v1")
        self.assertEqual(report["run"]["job_id"], FAILED_JOB_ID)
        self.assertEqual(report["run"]["entry_id"], ENTRY_ID)
        self.assertEqual(report["run"]["status"], "failed")
        self.assertEqual(report["run"]["mode"], "core-first")
        self.assertEqual(report["run"]["selected_route"], "core")
        self.assertEqual(report["terminal"]["diagnosed_failure_type"], "output_truncated")
        self.assertEqual(report["terminal"]["recorded_failure_type"], "candidate_no_change")
        self.assertNotEqual(report["terminal"]["usage"]["measurement"], "unavailable")
        self.assertEqual(report["terminal"]["usage"]["measurement"], "inferred-from-stderr")
        self.assertEqual(report["terminal"]["usage"]["completion_tokens"], 6000)
        self.assertEqual(report["counts"]["provider_attempt_count"], 1)
        self.assertEqual(report["counts"]["logical_stage_count"], 2)
        self.assertEqual(report["verification_summary"]["exit_code"], 3)

    def test_steps_present_and_honest(self):
        job = _failed_job()
        entry = self.lib / "entries" / ENTRY_ID
        report = report_mod.build_report(job, entry, self.lib, job_id=FAILED_JOB_ID)
        step_ids = [s["step_id"] for s in report["steps"]]
        self.assertEqual(step_ids, [f"P{i:02d}" for i in range(16)])
        by_id = {s["step_id"]: s for s in report["steps"]}
        self.assertEqual(by_id["P07"]["verification_result"], "failed")
        self.assertEqual(by_id["P08"]["verification_result"], "failed")
        self.assertEqual(by_id["P06"]["verification_result"], "failed")
        self.assertIn("request_preflight=missing", by_id["P06"]["verification_note"])
        self.assertIn("缺失", by_id["P00"]["verification_note"])
        # 失败作业的 P09 物理 Gate 不得伪造 passed
        self.assertIn(by_id["P09"]["verification_result"], ("not-run", "provisional", "failed"))

    def test_report_json_free_of_sensitive_patterns(self):
        job = _failed_job()
        entry = self.lib / "entries" / ENTRY_ID
        report = report_mod.build_report(job, entry, self.lib, job_id=FAILED_JOB_ID)
        text = json.dumps(report, ensure_ascii=False)
        for forbidden in ("api_key", "Authorization", "data:"):
            self.assertNotIn(forbidden, text)
        self.assertIsNone(re.search(r"(?<!\w)sk-", text))
        self.assertEqual(report_mod.leak_scan(report), [])

    def test_verify_mode_prints_gate_matrix_and_exits_3(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = report_mod.main(["--job-id", FAILED_JOB_ID, "--format", "markdown", "--verify"], library=self.lib)
        self.assertEqual(code, 3)
        self.assertIn("## 逐步验证（--verify）", out.getvalue())
        self.assertIn("P00", out.getvalue())
        self.assertIn("output_truncated", out.getvalue())


class CompletedJobReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = _library(Path(self.tmp.name))
        entry = _entry(self.lib)
        for name in ("solution.md", "student-solution.md", "teacher-solution.md"):
            (entry / name).write_text("# 解析\n", encoding="utf-8")
        _write_job(self.lib, _completed_job(self.lib))

    def test_completed_job_exit_zero(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = report_mod.main(["--job-id", COMPLETED_JOB_ID, "--format", "json"], library=self.lib)
        self.assertIn(code, (0, 2))
        report = json.loads(out.getvalue())
        self.assertEqual(report["terminal"]["usage"]["measurement"], "provider-reported")
        self.assertEqual(report["verification_summary"]["route_snapshot"]["status"], "current")
        # 模型显示名（如 dpsk-pro）不进入报告；全文无敏感痕迹（sk- 前缀感知）
        self.assertNotIn("dpsk-pro", json.dumps(report, ensure_ascii=False))
        self.assertEqual(report_mod.leak_scan(report), [])

    def test_completed_job_without_snapshot_is_evidence_gap(self):
        # 历史未埋点作业没有 route_snapshot → P00 not-run → 证据不完整 → 3
        job = _completed_job(self.lib, snapshot=False)
        _write_job(self.lib, job)
        code = _run_cli(self.lib, "--job-id", COMPLETED_JOB_ID, "--format", "json")
        self.assertEqual(code, 3)

    def test_counts_match_attempts(self):
        job = _completed_job(self.lib)
        entry = self.lib / "entries" / ENTRY_ID
        report = report_mod.build_report(job, entry, self.lib, job_id=COMPLETED_JOB_ID)
        self.assertEqual(report["counts"]["provider_attempt_count"], len(job["result"]["attempts"]))
        self.assertTrue(report["verification_summary"]["count_consistency"]["ok"])


class InputErrorTest(unittest.TestCase):
    def test_missing_job_id_returns_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = _library(Path(tmp))
            code = _run_cli(lib, "--job-id", "0" * 32, "--format", "json")
            self.assertEqual(code, 4)

    def test_malformed_job_id_returns_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = _library(Path(tmp))
            code = _run_cli(lib, "--job-id", "../../etc/passwd", "--format", "json")
            self.assertEqual(code, 4)

    def test_entry_id_without_latest_returns_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = _library(Path(tmp))
            code = _run_cli(lib, "--entry-id", ENTRY_ID, "--format", "json")
            self.assertEqual(code, 4)


class W3CountsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = _library(Path(self.tmp.name))
        entry = _entry(self.lib)
        (entry / "w3-shadow-report.json").write_text(json.dumps(_w3_report(), ensure_ascii=False), encoding="utf-8")
        self.job = _completed_job(self.lib)
        self.job["id"] = "ccccccccccccccccccccccccccccc001"
        self.job["result"].pop("adaptive_routing", None)
        self.job["result"]["attempts"] = []
        _write_job(self.lib, self.job)

    def test_w3_counts_semantics(self):
        entry = self.lib / "entries" / ENTRY_ID
        report = report_mod.build_report(self.job, entry, self.lib, job_id=self.job["id"])
        counts = report["counts"]
        self.assertEqual(counts["logical_stage_count"], 5)
        self.assertEqual(counts["checkpoint_replay_count"], 3)
        self.assertEqual(counts["supplemental_analysis_count"], 2)  # solver-b + adjudicator
        self.assertEqual(counts["rollback_count"], 0)
        self.assertFalse(report["verification_summary"]["rollback_observed"])
        self.assertEqual(report["run"]["mode"], "w3-shadow")

    def test_w3_steps_carried_from_report(self):
        entry = self.lib / "entries" / ENTRY_ID
        report = report_mod.build_report(self.job, entry, self.lib, job_id=self.job["id"])
        by_id = {s["step_id"]: s for s in report["steps"]}
        self.assertEqual(by_id["P09"]["verification_result"], "passed")  # verifier completed
        self.assertEqual(by_id["P10"]["verification_result"], "passed")  # distinct identity
        self.assertEqual(by_id["P14"]["verification_result"], "passed")  # shadow 不提升
        self.assertIn("w3-shadow-report.json", by_id["P09"]["artifact_refs"])

    def test_rollback_never_counts_without_evidence(self):
        # 无 Challenge/最小锥/重执行证据时 rollback_count 必须为 0
        job = _failed_job()
        job["result"]["attempts"] = []
        entry = self.lib / "entries" / ENTRY_ID
        report = report_mod.build_report(job, entry, self.lib, job_id=FAILED_JOB_ID)
        self.assertEqual(report["counts"]["rollback_count"], 0)
        self.assertFalse(report["verification_summary"]["rollback_observed"])


class LatestJobTest(unittest.TestCase):
    def test_entry_id_latest_resolves_newest_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = _library(Path(tmp))
            _entry(lib)
            older = _failed_job()
            older["id"] = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaa001"
            older["created_at"] = "2026-08-02T18:00:00+08:00"
            older["completed_at"] = "2026-08-02T18:01:00+08:00"
            newer = _failed_job()
            newer["id"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbb001"
            newer["created_at"] = "2026-08-02T19:00:00+08:00"
            newer["completed_at"] = "2026-08-02T19:01:00+08:00"
            _write_job(lib, older)
            _write_job(lib, newer)
            out = io.StringIO()
            with redirect_stdout(out):
                code = report_mod.main(["--entry-id", ENTRY_ID, "--latest", "--format", "json"], library=lib)
            self.assertEqual(code, 3)  # 最新作业仍是失败作业
            report = json.loads(out.getvalue())
            self.assertEqual(report["run"]["job_id"], newer["id"])

    def test_entry_id_latest_with_no_job_returns_4(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = _library(Path(tmp))
            code = _run_cli(lib, "--entry-id", "no-such-entry-00000000", "--latest", "--format", "json")
            self.assertEqual(code, 4)


if __name__ == "__main__":
    unittest.main()
