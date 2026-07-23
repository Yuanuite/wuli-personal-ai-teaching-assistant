import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import candidate_archive  # noqa: E402
import evaluator  # noqa: E402
import kb  # noqa: E402
import knowledge_store  # noqa: E402


class KnowledgeStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "student-error-library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260722-knowledge-store"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        (assets / "original.png").write_bytes(b"source")
        (assets / "explanation.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80"></svg>',
            encoding="utf-8",
        )
        solution = (
            "# 解析\n\n"
            "## 答案速览\n用动量守恒和能量守恒处理碰撞。\n\n"
            "## 详细解答\n先判断系统外力冲量可忽略，再列动量守恒式，最后用能量关系检查结果。"
            "这个步骤用于测试本地 RAG 证据检索。\n\n"
            "## 易错点\n不要把机械能守恒误用于非弹性碰撞。\n\n"
            "![解释图](assets/explanation.svg)\n"
        )
        for name, text in {
            "problem.md": "# 碰撞测试题\n\n小车与木块发生非弹性碰撞，求共同速度。",
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
                "status": "ready",
                "answer_status": "complete",
                "title": "动量守恒碰撞题",
                "subject": "高中物理",
                "grade": "高二",
                "knowledge_points": ["动量守恒", "非弹性碰撞"],
                "error_types": ["误用机械能守恒"],
                "difficulty": "3",
                "created_at": "2026-07-22T09:00:00+08:00",
                "updated_at": "2026-07-22T09:00:00+08:00",
                "source": {
                    "sha256": hashlib.sha256(b"source").hexdigest(),
                    "source_type": "png",
                    "stored_files": ["assets/original.png"],
                },
                "ocr": {"engine": "test", "review_required": False},
                "source_review": {"status": "passed"},
            },
        )
        answer_review = {
            "schema_version": 1,
            "entry_id": self.entry.name,
            "status": "passed",
            "reviewer": "teacher",
            "reviewed_at": "2026-07-22T10:00:00+08:00",
            "answer_digest": kb.answer_artifact_digest(self.entry),
            "note": "checked",
        }
        kb.write_json(self.entry / "answer-review.json", answer_review)
        record = kb.load_json(self.entry / "record.json", {})
        record["answer_review"] = answer_review
        kb.write_json(self.entry / "record.json", record)
        self.evaluation = evaluator.evaluate_entry(self.library, self.entry.name, write=True)
        candidate_archive.append_event(
            self.library,
            self.entry,
            task_type="answer.save",
            actor="teacher",
            event_type="manual-edit",
            status="saved",
            summary="教师调整学生版解析",
            evaluation=self.evaluation,
            changed_files=["student-solution.md"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_rebuild_creates_sqlite_store_with_entry_evidence(self):
        report = knowledge_store.rebuild(self.library)
        self.assertEqual(report["status"], "rebuilt")
        self.assertEqual(report["entries"], 1)
        self.assertTrue((self.library / "indexes" / "wuli-memory.db").is_file())
        evidence = knowledge_store.query(self.library, "动量守恒 非弹性碰撞", mode="teaching", top_k=3)
        self.assertEqual(evidence["results"][0]["entry_id"], self.entry.name)
        self.assertEqual(evidence["results"][0]["evaluation"]["status"], self.evaluation["status"])
        self.assertEqual(evidence["results"][0]["recent_events"][0]["task_type"], "answer.save")
        self.assertIn("entries/20260722-knowledge-store", evidence["evidence_sources"][0])
        self.assertEqual(evidence["evolve_observations"], [])

    def test_kb_rebuild_refreshes_knowledge_store_fail_soft(self):
        report = kb.rebuild_index(self.library)
        self.assertEqual(report["knowledge_store"]["status"], "rebuilt")
        self.assertTrue((self.library / "indexes" / "wuli-memory.db").is_file())

    def test_agent_evidence_excludes_current_entry_and_internal_paths(self):
        similar = self.library / "entries" / "20260722-similar-private-id"
        similar.mkdir(parents=True)
        kb.write_text(similar / "problem.md", "# 相似碰撞题\n\n两个小车发生非弹性碰撞，求共同速度。")
        kb.write_text(similar / "solution.md", "使用动量守恒，先规定正方向，再检查单位和极限情况。")
        kb.write_json(similar / "record.json", {
            "schema_version": 1,
            "id": similar.name,
            "kind": "error",
            "status": "ready",
            "title": "同类动量守恒题",
            "subject": "高中物理",
            "grade": "高二",
            "knowledge_points": ["动量守恒", "非弹性碰撞"],
            "error_types": ["方向符号错误"],
            "methods": ["规定正方向后列动量守恒"],
            "library_folder": "学生私有文件夹",
        })
        knowledge_store.rebuild(self.library)
        evidence = knowledge_store.build_agent_evidence(
            self.library,
            self.entry.name,
            "动量守恒 非弹性碰撞 共同速度",
            task_type="answer.revise",
            top_k=2,
            char_budget=4000,
        )
        self.assertEqual(evidence["status"], "ready")
        self.assertEqual(evidence["references"][0]["title"], "同类动量守恒题")
        serialized = json.dumps(evidence, ensure_ascii=False)
        self.assertNotIn(self.entry.name, serialized)
        self.assertNotIn(similar.name, serialized)
        self.assertNotIn("学生私有文件夹", serialized)
        self.assertNotIn("wuli-memory.db", serialized)

    def test_query_additively_migrates_an_older_derived_database(self):
        knowledge_store.rebuild(self.library)
        target = knowledge_store.db_path(self.library)
        connection = knowledge_store.connect(target)
        try:
            connection.execute("DROP TABLE scheduler_benchmark")
            connection.commit()
        finally:
            connection.close()
        evidence = knowledge_store.query(self.library, "动量守恒", mode="teaching", top_k=2)
        self.assertTrue(evidence["results"])
        connection = knowledge_store.connect(target)
        try:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduler_benchmark'"
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(table)


if __name__ == "__main__":
    unittest.main()
