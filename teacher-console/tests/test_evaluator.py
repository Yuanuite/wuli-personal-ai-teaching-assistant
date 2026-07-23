import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import evaluator  # noqa: E402
import kb  # noqa: E402
import process_uploads  # noqa: E402


class EvaluatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "student-error-library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260722-evaluator-sample"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        (assets / "original.png").write_bytes(b"source")
        (assets / "explanation.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80"></svg>',
            encoding="utf-8",
        )
        problem = "# 测试题\n\n这是一道用于 evaluator 测试的高中物理题，题干长度足够，并且已经由教师核对。"
        solution = (
            "# 解析\n\n"
            "## 答案速览\n结论是先判断物理过程，再代入公式。\n\n"
            "## 详细解答\n"
            "第一步，确认研究对象和条件；第二步，写出高中物理常用关系；"
            "第三步，代入并检查单位、方向和边界。这里补足解释文字，保证答案足够完整。\n\n"
            "## 易错点\n容易忽略条件和方向。\n\n"
            "![解释图](assets/explanation.svg)\n"
        )
        for name, text in {
            "problem.md": problem,
            "solution.md": solution,
            "student-solution.md": solution,
            "teacher-solution.md": solution,
        }.items():
            kb.write_text(self.entry / name, text)
        kb.write_json(
            self.entry / "record.json",
            {
                "schema_version": 1,
                "id": self.entry.name,
                "kind": "error",
                "status": "needs-review",
                "answer_status": "pending",
                "title": "Evaluator 测试题",
                "subject": "高中物理",
                "knowledge_points": ["测试知识点"],
                "error_types": ["测试错因"],
                "created_at": "2026-07-22T09:00:00+08:00",
                "updated_at": "2026-07-22T09:00:00+08:00",
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

    def approve_answer(self):
        result = process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        self.assertEqual(result["status"], "approved")

    def test_evaluate_writes_entry_report_before_delivery(self):
        self.approve_answer()
        report = evaluator.evaluate_entry(self.library, self.entry.name)
        self.assertEqual(report["status"], "passed")
        self.assertTrue((self.entry / "evaluation.json").is_file())
        self.assertEqual(report["checks"][0]["id"], "entry_structure")
        self.assertEqual(report["summary"]["failed"], 0)
        self.assertIn("correctness", report["scores"])

    def test_approve_answer_auto_refreshes_evaluation(self):
        approved = process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        self.assertEqual(approved["evaluation"]["status"], "passed")
        report = kb.load_json(self.entry / "evaluation.json", {})
        self.assertEqual(report["entry_id"], self.entry.name)
        self.assertEqual(report["status"], "passed")

    def test_revision_request_auto_refreshes_failed_evaluation(self):
        self.approve_answer()
        requested = process_uploads.request_answer_revision(self.library, self.entry.name, "teacher", "请补充步骤")
        self.assertEqual(requested["status"], "revision-requested")
        self.assertEqual(requested["evaluation"]["status"], "failed")
        report = kb.load_json(self.entry / "evaluation.json", {})
        self.assertTrue(
            any(item["id"] == "answer_review_current" and item["status"] == "failed" for item in report["checks"])
        )

    def test_stale_answer_review_fails_evaluation(self):
        self.approve_answer()
        kb.write_text(
            self.entry / "student-solution.md",
            (self.entry / "student-solution.md").read_text(encoding="utf-8") + "\n新增内容。\n",
        )
        report = evaluator.evaluate_entry(self.library, self.entry.name, write=False)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(
            any(item["id"] == "answer_review_current" and item["status"] == "failed" for item in report["checks"])
        )
        self.assertTrue(report["teacher_review_required"])

    def test_finish_records_evaluation_in_manifest_and_output(self):
        self.approve_answer()
        output = Path(self.temp.name) / "output"

        def fake_export(_root, _entry_id, _output_base):
            output.mkdir(parents=True, exist_ok=True)
            kb.write_text(output / "带答案错题.md", "student")
            return {
                "entry_id": self.entry.name,
                "output": str(output),
                "pdf": {"status": "generated", "file": "带答案错题.pdf"},
            }

        with (
            mock.patch.object(kb, "finalize_entry", return_value=[]),
            mock.patch.object(kb, "export_entry", side_effect=fake_export),
        ):
            manifest = process_uploads.finish(self.library, self.entry.name, None, "auto")
        self.assertEqual(manifest["status"], "delivered")
        self.assertEqual(manifest["evaluation"]["status"], "passed")
        self.assertIn("evaluation.json", manifest["files"])
        self.assertTrue((output / "evaluation.json").is_file())
        self.assertEqual(kb.load_json(self.entry / "evaluation.json", {})["entry_id"], self.entry.name)


if __name__ == "__main__":
    unittest.main()
