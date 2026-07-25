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
                "- 最短主线：确定研究对象 → 应用守恒关系。\n\n"
                "## 详细解答\n\n### 第 1 步\n\n建立方程并求解。\n\n"
                "## 易错点\n\n- 不要漏掉方向。\n\n"
                "## 30 秒自测\n\n方向改变时符号如何变化？"
            ),
            "teacher_audit": "- 量纲检查：各物理量单位一致。\n- 边界情况：极限条件下结论仍然成立。",
            "method_check": {
                "selected_path": "确定研究对象后直接应用机械能守恒。",
                "high_school_basis": ["机械能守恒", "量纲检查"],
                "discarded_methods": ["舍弃逐时刻动力学展开"],
                "student_step_count": 1,
            },
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

    def test_output_contract_guides_complex_electricity_diagrams(self):
        instructions = analysis_artifacts.output_contract()["instructions"]

        self.assertIn("等效电路", instructions)
        self.assertIn("电动势源", instructions)
        self.assertIn("内阻", instructions)
        self.assertIn("\\notag", instructions)
        self.assertIn("aqquad", instructions)
        self.assertIn("端电压", instructions)
        self.assertIn("method_check", instructions)
        self.assertIn("禁止使用积分", instructions)
        self.assertIn("证据优先于层级模板", instructions)
        self.assertIn("每个小问和每个选项判断", instructions)
        self.assertIn("并不对应", instructions)
        self.assertIn("易错点最多三条", instructions)

    def test_rejects_broken_latex_escape_fragments(self):
        payload = self.payload()
        payload["student_solution"] = payload["student_solution"].replace(
            "建立方程并求解。",
            "$$\\begin{aligned}v&=v_0\\\\\notag\n&=2v_0\\end{aligned}$$\n\naqquad",
        )

        with self.assertRaisesRegex(ValueError, "broken LaTeX fragment"):
            analysis_artifacts.normalize_payload(payload)

    def test_rejects_advanced_or_overlong_student_method(self):
        payload = self.payload()
        payload["student_solution"] = payload["student_solution"].replace(
            "建立方程并求解。",
            "使用积分 $W=\\int F\\,dx$ 求解。",
        )
        with self.assertRaisesRegex(ValueError, "non-high-school method: 积分"):
            analysis_artifacts.normalize_payload(payload)

        payload = self.payload()
        extra_steps = "\n".join(f"### 第 {index} 步\n\n代入关系。" for index in range(1, 7))
        payload["student_solution"] = payload["student_solution"].replace(
            "### 第 1 步\n\n建立方程并求解。",
            extra_steps,
        )
        payload["method_check"]["student_step_count"] = 5
        with self.assertRaisesRegex(ValueError, "maximum is 5"):
            analysis_artifacts.normalize_payload(payload)

    def test_rejects_method_check_step_count_mismatch(self):
        payload = self.payload()
        payload["method_check"]["student_step_count"] = 2
        with self.assertRaisesRegex(ValueError, "does not match"):
            analysis_artifacts.normalize_payload(payload)

    def test_existing_physics_model_blocks_wrong_options_and_preserves_physical_svg(self):
        (self.entry / "physics-model.json").write_text(
            json.dumps({
                "student_solution": {
                    "quick_answers": ["A 错；B、C、D 正确"],
                }
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        assets = self.entry / "assets"
        assets.mkdir(exist_ok=True)
        physical_svg = '<svg xmlns="http://www.w3.org/2000/svg"><path id="trajectory"/></svg>\n'
        (assets / "explanatory.svg").write_text(physical_svg, encoding="utf-8")

        wrong = self.payload()
        wrong["student_solution"] = wrong["student_solution"].replace(
            "- 答案为 $v$。",
            "- A 错；B 对；C 错；D 错。",
        )
        with self.assertRaisesRegex(ValueError, "contradicts physics-model.*C, D"):
            analysis_artifacts.materialize(self.entry, wrong)

        correct = self.payload()
        correct["student_solution"] = correct["student_solution"].replace(
            "- 答案为 $v$。",
            "- A 错；B、C、D 对。",
        )
        analysis_artifacts.materialize(self.entry, correct)
        self.assertEqual(
            (assets / "explanatory.svg").read_text(encoding="utf-8"),
            physical_svg,
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

        evidence_fingerprint = analysis_artifacts.input_fingerprint(
            self.entry,
            instruction="生成解析",
            model_id="model-1",
            routing_tier="auto",
            evidence_digest="sha256:new-evidence",
        )
        self.assertNotEqual(changed_fingerprint, evidence_fingerprint)


if __name__ == "__main__":
    unittest.main()
