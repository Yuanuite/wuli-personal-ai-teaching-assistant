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

SERVER_SPEC = importlib.util.spec_from_file_location("teacher_console_server", ROOT / "teacher-console" / "server.py")
teacher_console_server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(teacher_console_server)


class AnswerReviewGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260719-review-gate"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        original = assets / "original.png"
        original.write_bytes(b"test-source")
        (assets / "explanatory.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"></svg>',
            encoding="utf-8",
        )
        problem = "# 测试题\n\n这是一个长度足够的高中物理测试题干，用于验证教师答案复核摘要在答案修改后会自动失效。"
        solution = (
            "# 解析\n\n## 答案速览\n结论正确。\n\n## 详细解答\n"
            "第一步定义物理量，第二步列出规律，第三步完成计算，并用量纲和边界条件进行双重检查。"
            "这里补充足够的解释文字，确保正式答案结构可以通过交付前校验。\n\n"
            "## 易错点\n注意方向与适用条件。\n\n![解释图](assets/explanatory.svg)\n"
        )
        kb.write_text(self.entry / "problem.md", problem)
        kb.write_text(self.entry / "solution.md", solution)
        kb.write_text(self.entry / "student-solution.md", solution)
        kb.write_text(self.entry / "teacher-solution.md", solution)
        source_digest = hashlib.sha256(original.read_bytes()).hexdigest()
        kb.write_json(
            self.entry / "record.json",
            {
                "schema_version": 1,
                "id": self.entry.name,
                "kind": "error",
                "status": "needs-review",
                "answer_status": "pending",
                "title": "答案复核门禁测试",
                "subject": "高中物理",
                "knowledge_points": ["测试"],
                "error_types": ["待确认"],
                "source": {
                    "sha256": source_digest,
                    "source_type": "png",
                    "stored_files": ["assets/original.png"],
                },
                "ocr": {"engine": "test", "review_required": False},
                "source_review": {"status": "passed"},
                "answer_review": {"status": "not-ready"},
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_change_invalidates_approval_and_blocks_finish(self):
        self.assertEqual(process_uploads.pipeline_state(self.entry)["state"], "needs-answer-review")
        approved = process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(process_uploads.pipeline_state(self.entry)["state"], "ready-to-finish")

        with (self.entry / "teacher-solution.md").open("a", encoding="utf-8") as handle:
            handle.write("\n教师层新增修改。\n")

        state = process_uploads.pipeline_state(self.entry)
        self.assertEqual(state["state"], "needs-answer-review")
        self.assertEqual(state["answer_review"]["status"], "stale")
        result = process_uploads.finish(self.library, self.entry.name, None, "skip")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("answer changed after teacher approval; review it again", result["errors"])

    def test_page_edit_sets_manual_mode_and_preserves_layer_contract(self):
        model_path = self.entry / "physics-model.json"
        kb.write_json(
            model_path,
            {
                "schema_version": 1,
                "source": {"problem": "problem.md", "diagram": "assets/explanatory.svg"},
            },
        )
        before = process_uploads.answer_digest(self.entry)
        revised = (self.entry / "teacher-solution.md").read_text(encoding="utf-8") + "\n教师复核后补充边界条件检查。\n"
        result = teacher_console_server.save_answer_entry(
            self.library,
            self.entry,
            {"layer": "teacher", "markdown": revised, "base_digest": before},
        )
        self.assertEqual(result["status"], "saved")
        self.assertEqual((self.entry / "solution.md").read_text(encoding="utf-8"), revised)
        model = kb.load_json(model_path, {})
        self.assertEqual(model["source"]["answer_render_mode"], "manual")
        self.assertFalse(process_uploads.should_render_answers(model_path))
        self.assertEqual(kb.load_json(self.entry / "answer-review.json", {})["status"], "needs-review")
        self.assertEqual(process_uploads.pipeline_state(self.entry)["state"], "needs-answer-review")

    def test_explanatory_svg_change_invalidates_answer_approval(self):
        approved = process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        self.assertEqual(approved["status"], "approved")
        (self.entry / "assets" / "explanatory.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="60"><circle r="8"/></svg>',
            encoding="utf-8",
        )
        state = process_uploads.pipeline_state(self.entry)
        self.assertEqual(state["state"], "needs-answer-review")
        self.assertEqual(state["answer_review"]["status"], "stale")

    def test_answer_revision_records_request_when_agent_is_unavailable(self):
        handler = object.__new__(teacher_console_server.Handler)
        with mock.patch.object(teacher_console_server, "answer_revision_command", return_value=None):
            result = handler.run_answer_revision(
                self.entry,
                {"reviewer": "teacher", "note": "请把第二步拆开，并同步修正解释 SVG 中的方向箭头"},
            )
        self.assertEqual(result["status"], "awaiting-agent")
        request = kb.load_json(self.entry / "answer-revision-request.json", {})
        self.assertEqual(request["status"], "awaiting-agent")
        self.assertEqual(kb.load_json(self.entry / "answer-review.json", {})["status"], "revision-requested")

    def test_answer_revision_agent_result_returns_to_teacher_review(self):
        handler = object.__new__(teacher_console_server.Handler)

        def fake_agent_run(command, cwd, **_kwargs):
            teacher = Path(cwd) / "teacher-solution.md"
            teacher.write_text(teacher.read_text(encoding="utf-8") + "\n按教师意见补充分步说明。\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="updated answer", stderr="")

        with mock.patch.object(teacher_console_server, "answer_revision_command", return_value=["fake-agent"]), mock.patch.object(
            teacher_console_server.subprocess,
            "run",
            side_effect=fake_agent_run,
        ):
            result = handler.run_answer_revision(
                self.entry,
                {"reviewer": "teacher", "note": "请将第二步拆分为两个高中生容易跟上的步骤"},
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resulting_state"], "needs-answer-review")
        self.assertIn("teacher-solution.md", result["changed_files"])
        self.assertEqual(kb.load_json(self.entry / "answer-review.json", {})["status"], "needs-review")
        self.assertEqual(
            (self.entry / "solution.md").read_text(encoding="utf-8"),
            (self.entry / "teacher-solution.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
