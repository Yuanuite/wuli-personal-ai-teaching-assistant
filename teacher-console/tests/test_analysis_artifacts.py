import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "teacher-console"))

import analysis_artifacts  # noqa: E402
import kb  # noqa: E402


class AnalysisArtifactsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        self.entry = self.library / "entries" / "entry-1"
        self.entry.mkdir(parents=True)
        (self.entry / "problem.md").write_text("# 题目\n" + "已复核题干。" * 10, encoding="utf-8")
        (self.entry / "record.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "entry-1",
                    "title": "原题标题",
                    "subject": "高中物理",
                    "knowledge_points": ["待整理"],
                    "error_types": ["待整理"],
                    "ocr": {"review_required": False},
                    "source_review": {"status": "passed"},
                    "source": {"stored_files": []},
                    "protected": "keep",
                    "updated_at": "2026-01-01T00:00:00+08:00",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def payload():
        return {
            "status": "completed",
            "message": "解析完成",
            "student_solution": (
                "# 任意标题\n\n"
                "## 答案速览\n\n- 答案为 $v$。\n\n"
                "## 一眼识别\n\n抓住守恒关系。\n\n"
                "## 详细解答\n\n### 第 1 步\n\n建立方程并求解。\n\n"
                "## 易错点\n\n- 不要漏掉方向。\n\n"
                "## 30 秒自测\n\n方向改变时符号如何变化？"
            ),
            "teacher_audit": "- 量纲检查：各物理量单位一致。\n- 边界情况：极限条件下结论仍然成立。",
            "metadata": {
                "knowledge_points": ["机械能守恒"],
                "error_types": ["方向判断"],
                "difficulty": "中等",
                "grade": "高二",
                "title": "机械能守恒与方向判断",
            },
            "diagram": {
                "title": "解题主线",
                "nodes": ["识别过程", "建立守恒", "求解", "检查方向"],
            },
        }

    def test_materialization_merges_only_teaching_metadata_and_builds_diagram(self):
        result = analysis_artifacts.materialize(self.entry, self.payload())

        record = json.loads((self.entry / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["protected"], "keep")
        self.assertEqual(record["updated_at"], "2026-01-01T00:00:00+08:00")
        self.assertEqual(record["knowledge_points"], ["机械能守恒"])
        student = (self.entry / "student-solution.md").read_text(encoding="utf-8")
        teacher = (self.entry / "teacher-solution.md").read_text(encoding="utf-8")
        self.assertTrue(student.startswith("# 解析（学生版）"))
        self.assertIn("assets/explanatory.svg", student)
        self.assertIn("## 教师审计", teacher)
        self.assertEqual(teacher, (self.entry / "solution.md").read_text(encoding="utf-8"))
        self.assertIn("<svg", (self.entry / analysis_artifacts.EXPLANATION_PATH).read_text(encoding="utf-8"))
        self.assertEqual(result["contract"], analysis_artifacts.ANALYSIS_CONTRACT)
        self.assertEqual(
            kb.validate_entry(
                self.library,
                self.entry,
                ready_rules=True,
                require_answer_review=False,
            ),
            [],
        )

    def test_checkpoint_replays_only_when_inputs_match(self):
        fingerprint = analysis_artifacts.input_fingerprint(
            self.entry,
            instruction="生成解析",
            model_id="model-1",
            routing_tier="auto",
        )
        analysis_artifacts.save_generation_checkpoint(
            self.entry,
            fingerprint=fingerprint,
            payload=self.payload(),
        )
        loaded = analysis_artifacts.load_generation_checkpoint(
            self.entry,
            fingerprint=fingerprint,
        )
        self.assertEqual(loaded["metadata"]["title"], "机械能守恒与方向判断")

        (self.entry / "problem.md").write_text("题干已经变化。" * 10, encoding="utf-8")
        changed_fingerprint = analysis_artifacts.input_fingerprint(
            self.entry,
            instruction="生成解析",
            model_id="model-1",
            routing_tier="auto",
        )
        self.assertIsNone(
            analysis_artifacts.load_generation_checkpoint(
                self.entry,
                fingerprint=changed_fingerprint,
            )
        )


if __name__ == "__main__":
    unittest.main()
