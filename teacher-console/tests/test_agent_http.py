import hashlib
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "teacher-console"))

import kb  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from agent_jobs import AgentJobManager  # noqa: E402

SERVER_SPEC = importlib.util.spec_from_file_location(
    "teacher_console_server_http", ROOT / "teacher-console" / "server.py"
)
teacher_console_server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(teacher_console_server)


class AgentHttpTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260720-http-agent"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        source = assets / "original.png"
        source.write_bytes(b"source")
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
                    "sha256": hashlib.sha256(b"source").hexdigest(),
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
        }
        teacher_console_server.LIBRARY = self.library
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
        for name, value in self.originals.items():
            setattr(teacher_console_server, name, value)
        self.temp.cleanup()

    def request_json(self, path, *, method="GET", body=None):
        data = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
        headers = {"Content-Type": "application/json"}
        if method == "POST":
            headers["X-Teacher-Console"] = "1"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_and_async_analysis_job(self):
        status, health = self.request_json("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["agent"]["selected"], "adapter")

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

        job = queued["job"]
        for _attempt in range(100):
            _status, job = self.request_json(job["url"])
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["result"]["provider"], "adapter")
        self.assertEqual(job["result"]["requested_tier"], "economy")
        self.assertEqual(job["result"]["model"], "fake-economy")
        self.assertEqual(job["result"]["usage"]["total_tokens"], 150)
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
