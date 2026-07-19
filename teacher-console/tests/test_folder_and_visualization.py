import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kb  # noqa: E402
import process_uploads  # noqa: E402

SERVER_SPEC = importlib.util.spec_from_file_location("teacher_console_server_visual", ROOT / "teacher-console" / "server.py")
teacher_console_server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(teacher_console_server)


class FolderAndVisualizationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "student-error-library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260719-visual-review"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        source = assets / "original.png"
        source.write_bytes(b"source")
        (assets / "explanation.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"></svg>',
            encoding="utf-8",
        )
        problem = "# 测试题\n\n这是一道长度足够的物理测试题，用于验证可视化构建、教师复核和最终交付使用相同产物。"
        solution = (
            "# 解析\n\n## 答案速览\n结论。\n\n## 详细解答\n"
            "先建立物理模型，再列出规律并计算，最后检查方向、单位和边界条件。"
            "这里补足解释文字，使答案满足正式入库与交付要求。\n\n"
            "## 易错点\n注意适用条件。\n\n![解释图](assets/explanation.svg)\n"
        )
        for name, text in {
            "problem.md": problem,
            "solution.md": solution,
            "student-solution.md": solution,
            "teacher-solution.md": solution,
        }.items():
            kb.write_text(self.entry / name, text)
        kb.write_json(
            self.entry / "physics-model.json",
            {"schema_version": 1, "model_type": "test", "source": {"answer_render_mode": "manual"}},
        )
        kb.write_json(
            self.entry / "record.json",
            {
                "schema_version": 1,
                "id": self.entry.name,
                "kind": "error",
                "status": "needs-review",
                "answer_status": "pending",
                "title": "可视化复核测试",
                "subject": "高中物理",
                "knowledge_points": ["测试"],
                "error_types": ["待确认"],
                "created_at": "2026-07-19T09:00:00+08:00",
                "updated_at": "2026-07-19T09:00:00+08:00",
                "source": {
                    "sha256": hashlib.sha256(b"source").hexdigest(),
                    "source_type": "png",
                    "stored_files": ["assets/original.png"],
                },
                "ocr": {"engine": "test", "review_required": False},
                "source_review": {"status": "passed"},
                "answer_review": {"status": "not-ready"},
                "visualization_review": {"status": "not-ready"},
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def fake_build(self, entry, output_dir, _runtime_mode="auto"):
        output_dir.mkdir(parents=True, exist_ok=True)
        html = output_dir / "physics-simulator.html"
        package = output_dir / "physics-simulator.zip"
        html.write_text("<!doctype html><title>reviewed simulator</title>", encoding="utf-8")
        package.write_bytes(b"reviewed-zip")
        report = {
            "status": "ok",
            "model_digest": kb.sha256_file(entry / "physics-model.json"),
            "built_at": kb.now_iso(),
            "artifacts": {"html": str(html), "zip": str(package)},
            "runtime_check": {"status": "passed"},
        }
        kb.write_json(output_dir / "simulation-build.json", report)
        return report

    def test_folder_view_migrates_and_rename_preserves_entry(self):
        canonical = self.entry.resolve()
        updated_at = kb.load_json(self.entry / "record.json", {})["updated_at"]
        groups = kb.sync_library_folders(self.library)
        self.assertEqual(groups[0]["name"], "2026-07-19")
        link = self.library / "folders" / "2026-07-19" / self.entry.name
        self.assertTrue(link.is_symlink() or link.with_suffix(".entry.json").exists())

        result = kb.rename_library_folder(self.library, "2026-07-19", "暑假第一课")
        self.assertEqual(result["status"], "renamed")
        self.assertEqual(self.entry.resolve(), canonical)
        record = kb.load_json(self.entry / "record.json", {})
        self.assertEqual(record["library_folder"], "暑假第一课")
        self.assertEqual(record["updated_at"], updated_at)
        self.assertTrue((self.library / "folders" / "暑假第一课").is_dir())
        with self.assertRaises(ValueError):
            kb.validate_library_folder_name("../escape")

    def test_visualization_must_be_built_and_approved_before_delivery(self):
        answer = process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        self.assertEqual(answer["status"], "approved")
        self.assertEqual(process_uploads.pipeline_state(self.entry)["state"], "needs-visualization-build")

        with mock.patch.object(process_uploads, "build_simulator", side_effect=self.fake_build):
            prepared = process_uploads.prepare_visualization(self.library, self.entry.name)
        self.assertEqual(prepared["status"], "ok")
        self.assertEqual(process_uploads.pipeline_state(self.entry)["state"], "needs-visualization-review")

        approved = process_uploads.approve_visualization(self.library, self.entry.name, "teacher", "轨迹和控件已核对")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(process_uploads.pipeline_state(self.entry)["state"], "ready-to-finish")

        reviewed_html = (self.entry / "visualization" / "physics-simulator.html").read_bytes()
        output = Path(self.temp.name) / "output"

        def fake_export(_root, _entry_id, _output_base):
            output.mkdir(parents=True, exist_ok=True)
            kb.write_text(output / "带答案错题.md", "student")
            return {"entry_id": self.entry.name, "output": str(output), "pdf": {"status": "skipped"}}

        with mock.patch.object(kb, "finalize_entry", return_value=[]), mock.patch.object(kb, "export_entry", side_effect=fake_export):
            delivered = process_uploads.finish(self.library, self.entry.name, None, "auto")
        self.assertEqual(delivered["status"], "delivered")
        self.assertEqual((output / "simulation" / "physics-simulator.html").read_bytes(), reviewed_html)

    def test_model_change_invalidates_answer_and_visualization_reviews(self):
        process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        with mock.patch.object(process_uploads, "build_simulator", side_effect=self.fake_build):
            process_uploads.prepare_visualization(self.library, self.entry.name)
        process_uploads.approve_visualization(self.library, self.entry.name, "teacher", "checked")
        model = kb.load_json(self.entry / "physics-model.json", {})
        model["teacher_change"] = True
        kb.write_json(self.entry / "physics-model.json", model)
        state = process_uploads.pipeline_state(self.entry)
        self.assertEqual(state["state"], "needs-answer-review")
        self.assertEqual(state["answer_review"]["status"], "stale")
        self.assertEqual(state["visualization_review"]["status"], "stale")

    def test_visualization_chat_fails_closed_without_agent(self):
        process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        handler = object.__new__(teacher_console_server.Handler)
        with mock.patch.object(teacher_console_server, "visualization_command", return_value=None):
            result = handler.run_visualization_chat(self.entry, {"message": "请补充关键事件暂停，并检查正电荷偏转方向"})
        self.assertEqual(result["status"], "awaiting-agent")
        conversation = kb.load_json(self.entry / "visualization-conversation.json", {})
        self.assertEqual(conversation["messages"][0]["role"], "teacher")
        self.assertEqual(conversation["messages"][1]["status"], "awaiting-agent")
        self.assertFalse((self.entry / "visualization-review.json").exists())

    def test_no_model_keeps_optional_visualization_entry_without_blocking_delivery(self):
        (self.entry / "physics-model.json").unlink()
        approved = process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        self.assertEqual(approved["status"], "approved")
        state = process_uploads.pipeline_state(self.entry)
        self.assertEqual(state["state"], "ready-to-finish")
        self.assertEqual(state["visualization"]["kind"], "not-generated")
        self.assertEqual(state["visualization_review"]["status"], "not-required")
        self.assertEqual(process_uploads.prepare_visualization(self.library, self.entry.name)["status"], "needs-model")
        blocked = process_uploads.approve_visualization(self.library, self.entry.name, "teacher", "")
        self.assertEqual(blocked["status"], "blocked")
        output = Path(self.temp.name) / "no-model-output"

        def fake_export(_root, _entry_id, _output_base):
            output.mkdir(parents=True, exist_ok=True)
            kb.write_text(output / "带答案错题.md", "student")
            return {"entry_id": self.entry.name, "output": str(output), "pdf": {"status": "skipped"}}

        with mock.patch.object(kb, "finalize_entry", return_value=[]), mock.patch.object(kb, "export_entry", side_effect=fake_export):
            delivered = process_uploads.finish(self.library, self.entry.name, None, "auto")
        self.assertEqual(delivered["status"], "delivered")
        manifest = kb.load_json(output / "delivery-manifest.json", {})
        self.assertEqual(manifest["visualization_review"]["status"], "not-required")
        self.assertEqual(manifest["simulation"]["status"], "not-generated")
        self.assertFalse((output / "simulation").exists())

    def test_teacher_can_request_visualization_when_no_model_exists(self):
        (self.entry / "physics-model.json").unlink()
        process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        handler = object.__new__(teacher_console_server.Handler)
        with mock.patch.object(teacher_console_server, "visualization_command", return_value=None):
            result = handler.run_visualization_chat(
                self.entry,
                {"message": "我想为这道题生成一个可交互的可视化结果"},
            )
        self.assertEqual(result["status"], "awaiting-agent")
        request = kb.load_json(self.entry / "visualization-request.json", {})
        self.assertEqual(request["status"], "awaiting-agent")
        self.assertIn("生成一个可交互", request["message"])

    def test_explicit_request_can_create_model_and_build_preview(self):
        (self.entry / "physics-model.json").unlink()
        process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        handler = object.__new__(teacher_console_server.Handler)

        def fake_agent_run(command, cwd, **_kwargs):
            kb.write_json(
                Path(cwd) / "physics-model.json",
                {"schema_version": 1, "model_type": "test", "source": {"answer_render_mode": "manual"}},
            )
            return subprocess.CompletedProcess(command, 0, stdout="created physics model", stderr="")

        with mock.patch.object(teacher_console_server, "visualization_command", return_value=["fake-agent"]), mock.patch.object(
            teacher_console_server.subprocess,
            "run",
            side_effect=fake_agent_run,
        ), mock.patch.object(process_uploads, "build_simulator", side_effect=self.fake_build):
            result = handler.run_visualization_chat(
                self.entry,
                {"message": "我想为这道题生成一个可交互的可视化结果"},
            )
        self.assertEqual(result["status"], "completed")
        self.assertTrue((self.entry / "physics-model.json").exists())
        self.assertTrue((self.entry / "visualization" / "physics-simulator.html").exists())
        self.assertEqual(result["state"]["state"], "needs-answer-review")

    def test_visualization_chat_can_be_cleared(self):
        kb.write_json(
            self.entry / "visualization-conversation.json",
            {"schema_version": 1, "entry_id": self.entry.name, "messages": [{"role": "teacher", "content": "旧消息"}]},
        )
        handler = object.__new__(teacher_console_server.Handler)
        result = handler.clear_visualization_chat(self.entry)
        self.assertEqual(result["status"], "cleared")
        self.assertEqual(kb.load_json(self.entry / "visualization-conversation.json", {})["messages"], [])


if __name__ == "__main__":
    unittest.main()
