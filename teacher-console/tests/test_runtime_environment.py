import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "teacher-console"))

import runtime_environment  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402


class _Connection:
    def close(self):
        return None


class RuntimeEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"

    def tearDown(self):
        self.temp.cleanup()

    def test_local_proxy_and_codex_path_are_saved_and_resolved(self):
        codex = Path(self.temp.name) / "codex"
        codex.write_text("#!/bin/sh\n", encoding="utf-8")
        saved = runtime_environment.save_runtime_settings(
            self.library,
            {
                "codex_path": str(codex),
                "proxy": {"mode": "manual", "url": "http://127.0.0.1:7890"},
            },
        )
        self.assertEqual(saved["proxy"]["mode"], "manual")
        env = runtime_environment.resolved_environment(
            self.library,
            {"PATH": "/safe/bin", "HTTPS_PROXY": "http://old:1"},
        )
        self.assertEqual(env["TEACHER_CONSOLE_CODEX_PATH"], str(codex))
        self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:7890")
        self.assertEqual(env["ALL_PROXY"], "http://127.0.0.1:7890")

    def test_direct_mode_removes_all_proxy_variants(self):
        runtime_environment.save_runtime_settings(
            self.library,
            {"proxy": {"mode": "direct", "url": ""}},
        )
        base = {key: "proxy" for key in runtime_environment.PROXY_ENV_KEYS}
        env = runtime_environment.resolved_environment(self.library, base)
        self.assertFalse(runtime_environment.PROXY_ENV_KEYS.intersection(env))

    def test_web_settings_reject_remote_or_credentialed_proxy(self):
        for url in ("http://example.com:7890", "http://user:secret@127.0.0.1:7890"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                runtime_environment.save_runtime_settings(
                    self.library,
                    {"proxy": {"mode": "manual", "url": url}},
                )

    def test_public_snapshot_discovers_local_proxy_without_enabling_it(self):
        def connector(address, timeout):
            if address == ("127.0.0.1", 7890):
                return _Connection()
            raise OSError("closed")

        snapshot = runtime_environment.runtime_settings_public(
            self.library,
            base={},
            which=lambda _name: None,
            run=lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", ""),
            connector=connector,
        )
        detected = [item for item in snapshot["proxy_status"]["candidates"] if item["available"]]
        self.assertEqual(detected, [{"url": "http://127.0.0.1:7890", "available": True}])
        self.assertEqual(snapshot["proxy"]["mode"], "inherit")
        self.assertEqual(snapshot["proxy_status"]["active_url"], "")

    def test_gateway_reloads_saved_runtime_without_restart(self):
        first = Path(self.temp.name) / "codex-one"
        second = Path(self.temp.name) / "codex-two"
        first.write_text("", encoding="utf-8")
        second.write_text("", encoding="utf-8")
        runtime_environment.save_runtime_settings(
            self.library,
            {"codex_path": str(first), "proxy": {"mode": "inherit", "url": ""}},
        )
        gateway = AgentGateway(
            environ={"PATH": "/safe/bin"},
            which=lambda _name: None,
            run=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "codex 1.0", ""),
            environment_resolver=lambda: runtime_environment.resolved_environment(
                self.library, {"PATH": "/safe/bin"}
            ),
        )
        self.assertEqual(gateway.providers()[0].command, (str(first),))
        runtime_environment.save_runtime_settings(
            self.library,
            {"codex_path": str(second), "proxy": {"mode": "inherit", "url": ""}},
        )
        self.assertEqual(gateway.providers()[0].command, (str(second),))

    def test_runtime_probe_explains_available_proxy(self):
        snapshot = {
            "codex": {"available": True},
            "proxy_status": {"candidates": [{"url": "http://127.0.0.1:7890", "available": True}]},
        }
        diagnosis = runtime_environment.classify_runtime_probe(
            snapshot,
            {"live_probe": {"status": "failed", "reason": "stream disconnected before completion"}},
        )
        self.assertEqual(diagnosis["code"], "proxy_available")
        self.assertFalse(diagnosis["student_data_sent"])

    def test_runtime_probe_status_survives_reload_until_config_changes(self):
        runtime_environment.save_runtime_settings(
            self.library,
            {"proxy": {"mode": "manual", "url": "http://127.0.0.1:7890"}},
        )
        runtime_environment.update_runtime_probe_result(
            self.library,
            {"status": "passed", "code": "ready", "message": "运行正常"},
        )
        self.assertTrue(runtime_environment.runtime_settings_public(self.library)["probe_passed"])
        runtime_environment.save_runtime_settings(
            self.library,
            {"proxy": {"mode": "direct", "url": ""}},
        )
        changed = runtime_environment.runtime_settings_public(self.library)
        self.assertFalse(changed["probe_passed"])
        self.assertEqual(changed["probe_status"], "untested")


if __name__ == "__main__":
    unittest.main()
