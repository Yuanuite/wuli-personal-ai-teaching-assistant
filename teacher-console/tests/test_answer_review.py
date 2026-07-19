import hashlib
import importlib.util
import shutil
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
        unavailable = {"status": "unavailable", "provider": None, "attempts": [], "changed_files": [], "unauthorized_changes": []}
        with mock.patch.object(teacher_console_server.AGENT_GATEWAY, "run", return_value=unavailable):
            result = handler.run_answer_revision(
                self.entry,
                {"reviewer": "teacher", "note": "请把第二步拆开，并同步修正解释 SVG 中的方向箭头"},
            )
        self.assertEqual(result["status"], "awaiting-agent")
        request = kb.load_json(self.entry / "answer-revision-request.json", {})
        self.assertEqual(request["status"], "awaiting-agent")
        self.assertEqual(kb.load_json(self.entry / "answer-review.json", {})["status"], "revision-requested")

    def test_economy_revision_uses_minimum_context(self):
        kb.write_json(self.entry / "physics-model.json", {"schema_version": 1})
        request_path = self.entry / "answer-revision-request.json"
        kb.write_json(request_path, {"note": "简化步骤"})
        task = teacher_console_server.answer_revision_task(self.entry, "简化步骤", request_path, "economy")
        self.assertEqual(task["routing_tier"], "economy")
        self.assertNotIn("record.json", task["input_paths"])
        self.assertNotIn("physics-model.json", task["input_paths"])
        self.assertNotIn("physics-model.json", task["allowed_paths"])
        self.assertIn("assets/explanatory.svg", task["input_paths"])
        self.assertNotIn(".agent-context/library-skill.md", task["context_files"])
        self.assertIn(".agent-context/answer-template.md", task["context_files"])

    def test_expert_revision_can_share_existing_model(self):
        kb.write_json(self.entry / "physics-model.json", {"schema_version": 1})
        request_path = self.entry / "answer-revision-request.json"
        kb.write_json(request_path, {"note": "复核共同语义"})
        task = teacher_console_server.answer_revision_task(self.entry, "复核共同语义", request_path, "expert")
        self.assertIn("physics-model.json", task["input_paths"])
        self.assertIn("physics-model.json", task["allowed_paths"])
        self.assertIn(".agent-context/library-skill.md", task["context_files"])

    def test_answer_revision_agent_result_returns_to_teacher_review(self):
        handler = object.__new__(teacher_console_server.Handler)

        def fake_gateway_run(_task, _validator):
            teacher = self.entry / "teacher-solution.md"
            teacher.write_text(teacher.read_text(encoding="utf-8") + "\n按教师意见补充分步说明。\n", encoding="utf-8")
            (self.entry / "solution.md").write_bytes(teacher.read_bytes())
            return {
                "status": "completed",
                "provider": "fake-agent",
                "returncode": 0,
                "stdout": "updated answer",
                "stderr": "",
                "changed_files": ["solution.md", "teacher-solution.md"],
                "unauthorized_changes": [],
                "validation_errors": [],
                "attempts": [],
            }

        with mock.patch.object(teacher_console_server.AGENT_GATEWAY, "run", side_effect=fake_gateway_run):
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

    def test_agent_candidate_cannot_change_source_provenance(self):
        staging = Path(self.temp.name) / "staging"
        shutil.copytree(self.entry, staging)
        record = kb.load_json(staging / "record.json", {})
        record["source"]["stored_files"] = []
        kb.write_json(staging / "record.json", record)
        errors = teacher_console_server.validate_answer_candidate(staging, ["record.json"], self.entry)
        self.assertIn("record.json protected field changed: source", errors)

        record = kb.load_json(self.entry / "record.json", {})
        record["source"]["stored_files"] = []
        kb.write_json(self.entry / "record.json", record)
        self.assertIn("assets/original.png", teacher_console_server.source_asset_names(self.entry))

    def test_economy_answer_candidate_validates_without_record_context(self):
        staging = Path(self.temp.name) / "economy-staging" / self.entry.name
        staging.mkdir(parents=True)
        shutil.copy2(self.entry / "problem.md", staging / "problem.md")
        for name in ("solution.md", "student-solution.md", "teacher-solution.md"):
            shutil.copy2(self.entry / name, staging / name)
        (staging / "assets").mkdir()
        shutil.copy2(self.entry / "assets" / "explanatory.svg", staging / "assets" / "explanatory.svg")

        errors = teacher_console_server.validate_answer_candidate(
            staging,
            ["assets/explanatory.svg", "solution.md", "student-solution.md", "teacher-solution.md"],
            self.entry,
        )

        self.assertNotIn("record.json protected field changed: id", errors)
        self.assertNotIn("record.json: unsupported schema_version", errors)
        self.assertNotIn("record.json: title is required", errors)
        self.assertEqual(errors, [])

    def test_teacher_console_instance_lock_is_exclusive(self):
        first = teacher_console_server.acquire_instance_lock(self.library)
        try:
            with self.assertRaisesRegex(RuntimeError, "已有教师工作台"):
                teacher_console_server.acquire_instance_lock(self.library)
        finally:
            teacher_console_server.release_instance_lock(first)


if __name__ == "__main__":
    unittest.main()
