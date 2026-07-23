import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import candidate_archive  # noqa: E402
import kb  # noqa: E402
import process_uploads  # noqa: E402


class CandidateArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "student-error-library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260722-candidate-archive"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        (assets / "original.png").write_bytes(b"source")
        (assets / "explanation.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80"></svg>',
            encoding="utf-8",
        )
        problem = "# 测试题\n\n这是一道用于 candidate archive 测试的高中物理题，题干长度足够。"
        solution = (
            "# 解析\n\n"
            "## 答案速览\n结论。\n\n"
            "## 详细解答\n先分析条件，再列式求解，并检查单位方向。这里补足解释文字，使答案足够完整。\n\n"
            "## 易错点\n注意适用条件。\n\n"
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
                "title": "Candidate Archive 测试题",
                "subject": "高中物理",
                "knowledge_points": ["测试"],
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

    def read_events(self):
        return candidate_archive.read_events(self.entry)

    def test_append_event_sanitizes_secret_fields(self):
        event = candidate_archive.append_event(
            self.library,
            self.entry,
            task_type="agent.test",
            actor="agent",
            event_type="agent-result",
            status="failed",
            summary="测试脱敏",
            request={"api_key": "sk-secret", "prompt": "请修改答案"},
            result={"authorization": "Bearer secret", "message": "failed"},
        )
        self.assertEqual(event["request"]["api_key"], "[redacted]")
        self.assertEqual(event["result"]["authorization"], "[redacted]")
        self.assertTrue((self.library / "indexes" / "candidate-archive.jsonl").is_file())

    def test_approve_answer_records_archive_event(self):
        result = process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        self.assertEqual(result["status"], "approved")
        self.assertEqual(result["archive"]["task_type"], "answer.approve")
        events = self.read_events()
        self.assertEqual(events[-1]["task_type"], "answer.approve")
        self.assertEqual(events[-1]["evaluation"]["status"], "passed")

    def test_finish_records_delivery_archive_event(self):
        process_uploads.approve_answer(self.library, self.entry.name, "teacher", "checked")
        output = Path(self.temp.name) / "output"

        def fake_export(_root, _entry_id, _output_base):
            output.mkdir(parents=True, exist_ok=True)
            kb.write_text(output / "带答案错题.md", "student")
            return {"entry_id": self.entry.name, "output": str(output), "pdf": {"status": "generated", "file": "带答案错题.pdf"}}

        with mock.patch.object(kb, "finalize_entry", return_value=[]), mock.patch.object(kb, "export_entry", side_effect=fake_export):
            manifest = process_uploads.finish(self.library, self.entry.name, None, "auto")
        self.assertEqual(manifest["evaluation"]["archive_event_id"], self.read_events()[-1]["event_id"])
        self.assertEqual(self.read_events()[-1]["task_type"], "delivery.finish")
        library_events = [
            json.loads(line)
            for line in (self.library / "indexes" / "candidate-archive.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(library_events[-1]["task_type"], "delivery.finish")


if __name__ == "__main__":
    unittest.main()
