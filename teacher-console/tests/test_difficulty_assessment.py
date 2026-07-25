import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts" / "difficulty_assessment.py"
SPEC = importlib.util.spec_from_file_location("difficulty_assessment", SCRIPT)
difficulty = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(difficulty)


class DifficultyAssessmentTest(unittest.TestCase):
    def setUp(self):
        self.record = {"title": "交替电场与磁场中的粒子运动", "knowledge_points": ["电场", "磁场", "圆周运动", "动能定理"]}
        self.problem = """（1）求位置；（2）求做功；（3）求哪些时刻被捕获。交替电场、磁场和多区域分段运动，需分析周期、轨迹与临界条件。"""
        self.answer = "建立模型，列方程 $r=mv/(qB)$，分段计算并作图。"

    def test_auto_assessment_has_six_weighted_dimensions(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.answer)
        self.assertEqual(assessment["status"], "default-accepted")
        self.assertEqual(assessment["rubric_version"], "evidence-rubric.v2")
        self.assertEqual(assessment["dimension_scale"], {"min": 0, "max": 5, "step": 0.1})
        self.assertEqual(len(assessment["dimensions"]), 6)
        self.assertEqual(sum(item["weight"] for item in assessment["dimensions"]), 100)
        self.assertGreaterEqual(assessment["score"], 0)
        self.assertLessEqual(assessment["score"], 100)
        self.assertTrue(assessment["summary"])
        self.assertTrue(all(item["evidence"] and item["evidence"][0]["source"] and item["evidence"][0]["excerpt"] for item in assessment["dimensions"]))

    def test_teacher_edit_recomputes_total_and_validates_score_range(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.answer)
        edited = difficulty.normalize_teacher_edit({
            "generated_at": assessment["generated_at"], "summary": "教师校准后的难度总结。",
            "dimensions": [{**item, "score": 5.0} for item in assessment["dimensions"]],
        }, self.problem, self.answer)
        self.assertEqual(edited["status"], "teacher-edited")
        self.assertEqual(edited["score"], 100)
        invalid = {**edited, "dimensions": [{**item, "score": 5.05} for item in edited["dimensions"]]}
        with self.assertRaisesRegex(ValueError, "步长为 0.1"):
            difficulty.normalize_teacher_edit(invalid, self.problem, self.answer)

    def test_teacher_edit_accepts_fractional_tenths(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.answer)
        edited = difficulty.normalize_teacher_edit({
            "generated_at": assessment["generated_at"], "summary": "教师使用小数步长校准。",
            "dimensions": [{**item, "score": 3.7} for item in assessment["dimensions"]],
        }, self.problem, self.answer)
        self.assertEqual(edited["score"], 74)
        self.assertTrue(all(item["score"] == 3.7 for item in edited["dimensions"]))

    def test_digest_changes_when_answer_changes(self):
        assessment = difficulty.auto_assess(self.record, self.problem, self.answer)
        self.assertTrue(difficulty.current(assessment, self.problem, self.answer))
        self.assertFalse(difficulty.current(assessment, self.problem, self.answer + "补充边界条件"))

    def test_calibration_sample_scores_93_with_reviewed_process_evidence(self):
        fixture = json.loads((ROOT / "teacher-console/tests/fixtures/difficulty_calibration.json").read_text(encoding="utf-8"))["cases"][0]
        assessment = difficulty.auto_assess(
            fixture["record"],
            fixture["problem"],
            fixture["student_solution"],
            fixture["physics_model"],
        )
        self.assertEqual(assessment["score"], fixture["expected"]["score"])
        self.assertEqual(assessment["level"], fixture["expected"]["level"])
        self.assertEqual([item["score"] for item in assessment["dimensions"]], fixture["expected"]["dimensions"])
        sources = [item["evidence"][0]["source"] for item in assessment["dimensions"]]
        self.assertTrue(set(fixture["expected"]["required_evidence_sources"]).issubset(sources))


if __name__ == "__main__":
    unittest.main()
