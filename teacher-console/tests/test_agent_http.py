import base64
import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "teacher-console"))

import kb  # noqa: E402
import model_registry  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from agent_jobs import AgentJobManager  # noqa: E402

SERVER_SPEC = importlib.util.spec_from_file_location(
    "teacher_console_server_http", ROOT / "teacher-console" / "server.py"
)
teacher_console_server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(teacher_console_server)


class _AdapterForcingGateway(AgentGateway):
    """Always route Agent execution through the deterministic test adapter.

    Claim-evidence shadow resolves solver/verifier as claude identities in the
    registry (the provider gate requires claude/openai-compatible), but the
    test environment has no real claude CLI; this mirrors E2EAgentGateway in
    teacher-console/e2e/run_e2e.py.
    """

    def _task_environ(self, task=None):
        env = super()._task_environ(task)
        env["TEACHER_CONSOLE_AGENT_PROVIDER"] = "adapter"
        return env


class AgentHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260720-http-agent"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        source = assets / "original.png"
        source_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
            "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        source.write_bytes(source_bytes)
        kb.write_text(
            self.entry / "problem.md",
            "# 测试题\n\n这是一道长度足够的测试题干，用来验证后台 Agent HTTP 作业会立即返回并支持轮询。",
        )
        kb.write_json(
            self.entry / "record.json",
            {
                "schema_version": 1,
                "id": self.entry.name,
                "kind": "error",
                "status": "needs-review",
                "answer_status": "pending",
                "title": "后台任务测试",
                "subject": "高中物理",
                "knowledge_points": ["测试"],
                "error_types": ["待确认"],
                "source": {
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "source_type": "png",
                    "stored_files": ["assets/original.png"],
                },
                "ocr": {"engine": "test", "review_required": False},
                "source_review": {"status": "passed"},
                "answer_review": {"status": "not-ready"},
            },
        )
        self.originals = {
            "LIBRARY": teacher_console_server.LIBRARY,
            "AGENT_GATEWAY": teacher_console_server.AGENT_GATEWAY,
            "_JOB_MANAGER": teacher_console_server._JOB_MANAGER,
            "UPLOADS": teacher_console_server.UPLOADS,
            "MODEL_REGISTRY_LIBRARY": model_registry.LIBRARY,
        }
        teacher_console_server.LIBRARY = self.library
        teacher_console_server.UPLOADS = Path(self.temp.name) / "uploads"
        teacher_console_server.UPLOADS.mkdir()
        model_registry.LIBRARY = self.library
        adapter = ROOT / "teacher-console" / "tests" / "fixtures" / "fake_agent_adapter.py"
        teacher_console_server.AGENT_GATEWAY = AgentGateway(
            environ={
                "TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND": f"{sys.executable} {adapter}",
                "TEACHER_CONSOLE_AGENT_PROVIDER": "adapter",
            }
        )
        teacher_console_server._JOB_MANAGER = AgentJobManager(self.library / ".cache" / "agent-jobs", max_workers=1)
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), teacher_console_server.Handler)
        except PermissionError:
            teacher_console_server._JOB_MANAGER.shutdown(wait=True)
            model_registry.LIBRARY = self.originals.pop("MODEL_REGISTRY_LIBRARY")
            for name, value in self.originals.items():
                setattr(teacher_console_server, name, value)
            self.temp.cleanup()
            self.skipTest("当前沙箱禁止创建 loopback socket")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        teacher_console_server._JOB_MANAGER.shutdown(wait=True)
        model_registry.LIBRARY = self.originals.pop("MODEL_REGISTRY_LIBRARY")
        for name, value in self.originals.items():
            setattr(teacher_console_server, name, value)
        self.temp.cleanup()

    def request_json(self, path, *, method="GET", body=None):
        data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
        headers = {"Content-Type": "application/json"}
        if method == "POST":
            headers["X-Teacher-Console"] = "1"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AssertionError(
                f"{method} {path} returned HTTP {exc.code}: {detail}"
            ) from exc

    def test_health_and_async_analysis_job(self):
        status, health = self.request_json("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["agent"]["selected"], "adapter")
        identity = health["runtime_identity"]
        snapshot = health["runtime_identity_snapshot"]
        for key in (
            "server_started_at",
            "code_digest",
            "route_config_digest",
            "model_registry_digest",
        ):
            self.assertIn(key, identity)
            self.assertIn(key, snapshot)
        self.assertEqual(identity["schema_version"], 1)
        self.assertIsInstance(identity["analysis_route"], dict)
        self.assertIn("mode", identity["analysis_route"])
        self.assertIn("runtime_stale", health)
        blob = json.dumps(identity, ensure_ascii=False)
        for secret_word in ("api_key", "sk-", "DEEPSEEK", "MIMO", "Bearer"):
            self.assertNotIn(secret_word.lower(), blob.lower())

        status, probed = self.request_json(
            "/api/agent/providers/probe",
            method="POST",
            body={"provider": "adapter", "timeout_seconds": 10},
        )
        self.assertEqual(status, 200)
        self.assertEqual(probed["live_probe"]["status"], "passed")
        self.assertFalse(probed["live_probe"]["student_data_sent"])

        status, queued = self.request_json(
            f"/api/entries/{self.entry.name}/analyze",
            method="POST",
            body={"routing_tier": "economy"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(queued["status"], "queued")
        self.assertTrue(queued["job"]["url"].startswith("/api/jobs/"))
        self.assertEqual(queued["job"]["routing_tier"], "economy")
        snapshot = queued["job"].get("route_snapshot") or {}
        self.assertEqual(snapshot.get("schema"), "wuli.route-snapshot.v1")
        self.assertEqual(snapshot.get("kind"), "analysis.generate")
        self.assertTrue(snapshot.get("config_digest"))

        job = queued["job"]
        for _attempt in range(100):
            _status, job = self.request_json(job["url"])
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["provider"], "adapter")
        self.assertEqual(job["result"]["requested_tier"], "economy")
        self.assertEqual(job["result"]["model"], "fake-core-solver")
        self.assertEqual(job["result"]["usage"]["total_tokens"], 120)
        analysis_request = kb.load_json(self.entry / "analysis-request.json", {})
        self.assertEqual(analysis_request["adaptive_routing"]["selected_route"], "core")
        self.assertEqual(len(analysis_request["attempts"]), 1)
        self.assertEqual(analysis_request["diagram_task"]["status"], "not-run")
        self.assertIn(
            "deterministic-teaching-render",
            {stage["name"] for stage in analysis_request["stages"]},
        )
        self.assertTrue((self.entry / "core-solution.json").is_file())
        self.assertEqual(kb.load_json(self.entry / "answer-review.json", {})["status"], "needs-review")
        self.assertEqual(
            teacher_console_server.process_uploads.pipeline_state(self.entry)["state"], "needs-answer-review"
        )

        output = Path(self.temp.name) / "output"
        output.mkdir()
        (output / "带答案错题.md").write_text("student delivery", encoding="utf-8")
        (output / "private.json").write_text('{"private":true}', encoding="utf-8")
        kb.write_json(
            self.entry / "delivery.json",
            {
                "output": str(output),
                "files": ["带答案错题.md", "private.json"],
            },
        )
        with urllib.request.urlopen(
            f"{self.base}/api/download/{self.entry.name}/%E5%B8%A6%E7%AD%94%E6%A1%88%E9%94%99%E9%A2%98.md", timeout=3
        ) as response:
            self.assertEqual(response.status, 200)
        with self.assertRaises(urllib.error.HTTPError) as blocked:
            urllib.request.urlopen(f"{self.base}/api/download/{self.entry.name}/private.json", timeout=3)
        self.assertEqual(blocked.exception.code, 404)

    def test_run_upload_automatically_queues_source_clean(self):
        source = teacher_console_server.UPLOADS / "auto-clean.png"
        source.write_bytes((self.entry / "assets" / "original.png").read_bytes() + b"auto-clean")

        with mock.patch.object(
            teacher_console_server.Handler,
            "run_source_clean",
            return_value={"status": "completed", "changed_files": ["problem.md", "record.json"]},
        ):
            status, report = self.request_json(
                "/api/run-upload",
                method="POST",
                body={"filename": source.name, "ocr": "none"},
            )
            self.assertEqual(status, 200)
            ingested = next(item for item in report["results"] if item["status"] == "ingested")
            self.assertEqual(ingested["source_clean"]["status"], "queued")
            self.assertEqual(ingested["source_clean"]["job"]["kind"], "source.clean")
            job = ingested["source_clean"]["job"]
            for _attempt in range(100):
                _status, job = self.request_json(job["url"])
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "completed")

    def test_run_upload_survives_source_clean_queue_failure(self):
        source = teacher_console_server.UPLOADS / "manual-review-fallback.png"
        source.write_bytes((self.entry / "assets" / "original.png").read_bytes() + b"manual-fallback")

        with mock.patch.object(
            teacher_console_server,
            "queue_agent_job",
            side_effect=ValueError("测试模型不可用"),
        ):
            status, report = self.request_json(
                "/api/run-upload",
                method="POST",
                body={"filename": source.name, "ocr": "none"},
            )
        self.assertEqual(status, 200)
        ingested = next(item for item in report["results"] if item["status"] == "ingested")
        self.assertEqual(ingested["source_clean"]["status"], "not-started")
        self.assertIn("测试模型不可用", ingested["source_clean"]["errors"][0])

    def test_entry_api_exposes_complete_review_safe_claim_evidence(self):
        kb.write_json(
            self.entry / "w3-shadow-report.json",
            {
                "status": "completed",
                "report": {
                    "mode": "shadow",
                    "claim_evidence_shadow": {
                        "status": "completed",
                        "certificates": [{
                            "claim_id": "C1",
                            "claim_version": 1,
                            "verifier_kind": "independent-agent",
                            "check_type": "semantic",
                            "verdict": "insufficient",
                            "normalized_result": "尚缺边界复算",
                            "decisive_checks": ["已核对主方程"],
                            "input_fingerprint": "a" * 64,
                            "verifier_identity": {
                                "model_id": "private-model",
                                "provider": "private-provider",
                                "context_isolated": True,
                            },
                        }],
                        "aggregation": {
                            "status": "PROVISIONAL",
                            "final_claims": [{
                                "claim_id": "C1",
                                "claim_version": 1,
                                "target_ids": ["Q1"],
                                "statement": "完整暂定答案",
                                "conditions": ["允许越过边界后返回"],
                                "status": "candidate",
                                "obligation_ids": ["V1"],
                            }],
                            "claim_evidence": {
                                "claims": [{
                                    "id": "C1",
                                    "version": 1,
                                    "kind": "final",
                                    "statement": "完整暂定答案",
                                    "target_ids": ["Q1"],
                                    "stage_ids": ["P1"],
                                    "depends_on": [],
                                    "conditions": ["允许越过边界后返回"],
                                    "obligation_ids": ["V1"],
                                    "status": "candidate",
                                    "source": {
                                        "input_fingerprint": "secret",
                                        "task_id": "secret-task",
                                    },
                                }],
                                "assessments": [{
                                    "claim_id": "C1",
                                    "issues": ["required certificate groups are incomplete"],
                                }],
                            },
                            "interface_status": "provisional",
                            "interface_issues": [{
                                "code": "boundary-state",
                                "message": "需核对返回边界时的状态",
                            }],
                            "open_challenge_ids": [],
                            "active_hypothesis_ids": [],
                            "root_path_issues": [],
                        },
                        "metrics": {
                            "claim_count": 1,
                            "certificate_count": 1,
                            "verified_claim_count": 0,
                            "unresolved_claim_count": 1,
                        },
                        "semantic_audit": {
                            "raw_reasoning": "must stay private"
                        },
                    },
                },
            },
        )
        status, detail = self.request_json(f"/api/entries/{self.entry.name}")
        self.assertEqual(status, 200)
        snapshot = detail["w3_shadow"]["claim_evidence"]
        self.assertEqual(snapshot["aggregation_status"], "PROVISIONAL")
        self.assertEqual(
            snapshot["final_answers"][0]["statement"], "完整暂定答案"
        )
        self.assertEqual(len(snapshot["claims"]), 1)
        self.assertEqual(len(snapshot["certificates"]), 1)
        self.assertEqual(len(snapshot["unresolved_obligations"]), 2)
        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("must stay private", encoded)
        self.assertNotIn("private-model", encoded)
        self.assertNotIn("input_fingerprint", encoded)
        self.assertNotIn("secret-task", encoded)

    def test_w3_shadow_fake_adapter_covers_claim_outcomes_without_canonical_write(self):
        canonical = "# 已批准解析\n\n此内容不得被影子链路修改。\n"
        kb.write_text(self.entry / "student-solution.md", canonical)
        # Claim-evidence shadow requires claude/openai-compatible solver and
        # verifier identities with distinct models; execution still routes
        # through the deterministic adapter (mirrors E2E configure_w3_test_models).
        kb.write_json(
            self.library / "config" / "model-registry.json",
            {
                "schema_version": 1,
                "defaults": {
                    "analysis.generate": "w3-claude-solver",
                    "expert": "w3-claude-solver",
                    "claim.verify": "w3-claude-verifier",
                },
                "models": [
                    {
                        "id": "w3-claude-solver",
                        "provider": "claude",
                        "model": "w3-solver-model",
                        "capabilities": ["analysis.generate"],
                    },
                    {
                        "id": "w3-claude-verifier",
                        "provider": "claude",
                        "model": "w3-verifier-model",
                        "capabilities": ["claim.verify"],
                    },
                ],
            },
        )
        for model_id in ("w3-claude-solver", "w3-claude-verifier"):
            model_registry.update_model_probe_result(
                model_id,
                {"live_probe": {"status": "passed", "provider": "claude", "reason": ""}},
            )
        adapter = ROOT / "teacher-console" / "tests" / "fixtures" / "fake_agent_adapter.py"
        teacher_console_server.AGENT_GATEWAY = _AdapterForcingGateway(
            environ={
                "TEACHER_CONSOLE_AGENT_ADAPTER_COMMAND": f"{sys.executable} {adapter}",
                "TEACHER_CONSOLE_AGENT_PROVIDER": "adapter",
            }
        )
        previous_flag = os.environ.get("TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW")
        os.environ["TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW"] = "1"
        try:
            expectations = {
                "normal": ("[claim-normal]", "VERIFIED", 0, False),
                "conflict": ("[claim-conflict]", "UNRESOLVED", 1, True),
                "insufficient": ("[claim-insufficient]", "PROVISIONAL", 1, True),
                "fuse": ("[claim-fuse]", "PROVISIONAL", 1, True),
            }
            for name, (
                marker,
                expected_status,
                minimum_challenges,
                expected_fuse,
            ) in expectations.items():
                with self.subTest(name=name):
                    kb.write_text(
                        self.entry / "problem.md",
                        (
                            "# 复杂过程测试\n\n"
                            "粒子先经过边界，再返回区域，并要求求出全部可能结果与首次事件。"
                            f" {marker}"
                        ),
                    )
                    _status, queued = self.request_json(
                        f"/api/entries/{self.entry.name}/analyze-w3-shadow",
                        method="POST",
                        body={"routing_tier": "economy"},
                    )
                    job = queued["job"]
                    for _attempt in range(200):
                        _status, job = self.request_json(job["url"])
                        if job["status"] in {"completed", "failed"}:
                            break
                        time.sleep(0.01)
                    self.assertEqual(job["status"], "completed")
                    self.assertEqual(job["result"]["status"], "completed")
                    raw_report = kb.load_json(
                        self.entry / "w3-shadow-report.json", {}
                    )
                    self.assertEqual(raw_report["status"], "completed")
                    report = raw_report["report"]["claim_evidence_shadow"]
                    self.assertEqual(report["status"], "completed", raw_report)
                    self.assertEqual(
                        report["aggregation"]["status"], expected_status
                    )
                    self.assertGreaterEqual(
                        report["metrics"]["challenge_count"],
                        minimum_challenges,
                    )
                    self.assertEqual(
                        report["metrics"]["fuse_triggered"], expected_fuse
                    )
                    if expected_fuse:
                        self.assertIn(
                            report["loop"]["transition"]["action"],
                            {"strategy-fuse", "hard-fuse"},
                        )
                        self.assertNotEqual(
                            report["loop"]["transition"]["control"][
                                "terminal_status"
                            ],
                            "VERIFIED",
                        )
                    self.assertEqual(report["metrics"]["repeated_task_count"], 0)
                    self.assertEqual(
                        (self.entry / "student-solution.md").read_text(
                            encoding="utf-8"
                        ),
                        canonical,
                    )
        finally:
            if previous_flag is None:
                os.environ.pop("TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW", None)
            else:
                os.environ["TEACHER_CONSOLE_CLAIM_EVIDENCE_SHADOW"] = previous_flag

    def test_runtime_settings_and_diagnosis_stay_local(self):
        class RuntimeGateway:
            def __init__(self):
                self.invalidated = 0

            def invalidate_health(self):
                self.invalidated += 1

            def probe(self, provider, **_kwargs):
                self.provider = provider
                return {
                    "live_probe": {
                        "status": "passed",
                        "provider": "codex",
                        "reason": "",
                        "student_data_sent": False,
                    }
                }

        gateway = RuntimeGateway()
        teacher_console_server.AGENT_GATEWAY = gateway
        status, saved = self.request_json(
            "/api/agent/runtime",
            method="POST",
            body={
                "codex_path": "",
                "proxy": {"mode": "manual", "url": "http://127.0.0.1:7890"},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(saved["proxy"]["mode"], "manual")
        self.assertTrue((self.library / "config" / "agent-runtime.json").is_file())
        self.assertEqual(gateway.invalidated, 1)

        status, runtime = self.request_json("/api/agent/runtime")
        self.assertEqual(status, 200)
        self.assertEqual(runtime["proxy"]["url"], "http://127.0.0.1:7890")

        status, diagnosed = self.request_json(
            "/api/agent/runtime/diagnose",
            method="POST",
            body={"timeout_seconds": 10},
        )
        self.assertEqual(status, 200)
        self.assertEqual(diagnosed["diagnosis"]["status"], "passed")
        self.assertFalse(diagnosed["diagnosis"]["student_data_sent"])
        self.assertEqual(gateway.provider, "codex")


if __name__ == "__main__":
    unittest.main()
