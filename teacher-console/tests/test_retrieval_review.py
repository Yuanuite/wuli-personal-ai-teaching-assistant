import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import kb  # noqa: E402

SERVER_SPEC = importlib.util.spec_from_file_location("teacher_console_server_retrieval_review", ROOT / "teacher-console" / "server.py")
server = importlib.util.module_from_spec(SERVER_SPEC)
SERVER_SPEC.loader.exec_module(server)


class RetrievalReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "student-error-library"
        kb.init_library(self.library)
        self.entries = [
            self.make_entry("momentum-collision", "完全非弹性碰撞", "小车与木块发生完全非弹性碰撞，求碰后的共同速度。", "动量守恒", "误用机械能守恒"),
            self.make_entry("magnetic-circle", "带电粒子圆周运动", "正电荷垂直进入匀强磁场，求轨迹半径和运动时间。", "洛伦兹力", "方向判断错误"),
        ]
        cases = [{
            "schema_version": 1,
            "id": "retrieval-001",
            "query": "帮我找一道动量守恒的易错题",
            "category": "teacher_phrase",
            "review_status": "draft",
            "relevant_entry_ids": [self.entries[0].name],
        }]
        server.retrieval_benchmark.write_cases(server.retrieval_benchmark.default_cases_path(self.library), cases)

    def tearDown(self):
        self.temp.cleanup()

    def make_entry(self, entry_id, title, problem, knowledge, error_type):
        entry = self.library / "entries" / entry_id
        assets = entry / "assets"
        assets.mkdir(parents=True)
        (assets / "original.png").write_bytes(b"fake-image")
        kb.write_text(entry / "problem.md", f"# {title}\n\n![原始题图](assets/original.png)\n\n{problem}")
        kb.write_text(entry / "teacher-solution.md", "这是教师私有解析，不应出现在候选摘要里。")
        kb.write_json(entry / "record.json", {
            "schema_version": 1,
            "id": entry_id,
            "title": title,
            "status": "ready",
            "knowledge_points": [knowledge],
            "error_types": [error_type],
            "source": {"stored_files": ["assets/original.png"]},
        })
        return entry

    def test_snapshot_exposes_question_cards_without_private_solution(self):
        snapshot = server.retrieval_review_snapshot(self.library)
        self.assertEqual(len(snapshot["cases"]), 1)
        self.assertEqual(len(snapshot["candidates"]), 2)
        candidate = next(item for item in snapshot["candidates"] if item["id"] == "momentum-collision")
        self.assertEqual(candidate["title"], "完全非弹性碰撞")
        self.assertIn("共同速度", candidate["problem_excerpt"])
        self.assertNotIn("教师私有解析", candidate["problem_excerpt"])
        self.assertEqual(candidate["thumbnail"], "/api/entry-file/momentum-collision/assets/original.png")
        self.assertNotIn("path", snapshot)

    def test_save_approval_updates_the_private_fixed_set(self):
        snapshot = server.save_retrieval_review({
            "id": "retrieval-001",
            "query": "完全非弹性碰撞 动量守恒",
            "category": "problem_type",
            "relevant_entry_ids": ["momentum-collision", "magnetic-circle"],
            "review_status": "approved",
        }, self.library)
        case = snapshot["cases"][0]
        self.assertEqual(case["review_status"], "approved")
        self.assertEqual(case["relevant_entry_ids"], ["magnetic-circle", "momentum-collision"])
        self.assertEqual(snapshot["validation"]["status_counts"]["approved"], 1)
        persisted = server.retrieval_benchmark.load_cases(server.retrieval_benchmark.default_cases_path(self.library))[0]
        self.assertEqual(persisted["query"], "完全非弹性碰撞 动量守恒")

    def test_approval_rejects_empty_or_unknown_selection(self):
        base = {
            "id": "retrieval-001",
            "query": "动量守恒",
            "category": "knowledge_point",
            "review_status": "approved",
        }
        with self.assertRaisesRegex(ValueError, "至少勾选"):
            server.save_retrieval_review({**base, "relevant_entry_ids": []}, self.library)
        with self.assertRaisesRegex(ValueError, "不可用于评测"):
            server.save_retrieval_review({**base, "relevant_entry_ids": ["missing-entry"]}, self.library)


if __name__ == "__main__":
    unittest.main()
