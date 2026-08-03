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
                "high_school_basis": ["机械能守恒"],
                "discarded_methods": ["舍弃逐时刻动力学展开"],
                "physical_stages": ["物体在保守力作用下由初态运动到末态"],
                "reasoning_steps": ["确定研究对象并应用机械能守恒"],
                "decisive_relations": ["初态机械能等于末态机械能"],
                "representation_transforms": [],
                "condition_checks": ["核对机械能守恒的适用条件"],
                "type_distance": {
                    "mode": "direct_archetype",
                    "archetype": "机械能守恒基础题",
                    "recognition_barrier": "直接识别研究对象与守恒条件",
                    "novel_bridge": "",
                },
                "student_step_count": 1,
            },
            "metadata": {
                "knowledge_points": ["机械能守恒"],
                "error_types": ["方向判断"],
                "difficulty": "中等",
                "grade": "高二",
                "title": "机械能守恒与方向判断",
            },
            "diagram": None,
        }

    def test_materialization_builds_answer_but_does_not_own_diagram(self):
        result = analysis_artifacts.materialize(self.entry, self.payload())

        record = json.loads((self.entry / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["protected"], "keep")
        self.assertEqual(record["updated_at"], "2026-01-01T00:00:00+08:00")
        self.assertEqual(record["knowledge_points"], ["机械能守恒"])
        self.assertEqual(record["standard_solution_path"]["source"], "wuli.analysis.v2")
        self.assertEqual(record["standard_solution_path"]["reasoning_steps"], ["确定研究对象并应用机械能守恒"])
        self.assertEqual(record["standard_solution_path"]["type_distance"]["mode"], "direct_archetype")
        student = (self.entry / "student-solution.md").read_text(encoding="utf-8")
        teacher = (self.entry / "teacher-solution.md").read_text(encoding="utf-8")
        self.assertTrue(student.startswith("# 解析（学生版）"))
        self.assertIn("assets/explanatory.svg", student)
        self.assertIn("## 教师审计", teacher)
        self.assertEqual(teacher, (self.entry / "solution.md").read_text(encoding="utf-8"))
        self.assertFalse((self.entry / analysis_artifacts.EXPLANATION_PATH).exists())
        self.assertEqual(result["contract"], analysis_artifacts.ANALYSIS_CONTRACT)
        self.assertEqual(
            kb.validate_entry(self.library, self.entry, ready_rules=True, require_answer_review=False),
            ["solution.md: missing image assets/explanatory.svg"],
        )

    def test_output_contract_delegates_diagram_to_independent_scene_task(self):
        instructions = analysis_artifacts.output_contract()["instructions"]

        self.assertIn("diagram 是兼容旧适配器的废弃字段", instructions)
        self.assertIn("独立的强类型场景任务", instructions)
        self.assertIn("\\notag", instructions)
        self.assertIn("aqquad", instructions)
        self.assertIn("rac{", instructions)
        self.assertIn("rac34", instructions)
        self.assertIn("method_check", instructions)
        self.assertIn("禁止使用积分", instructions)
        self.assertIn("证据优先于层级模板", instructions)
        self.assertIn("每个小问和每个选项判断", instructions)
        self.assertIn("并不对应", instructions)
        self.assertIn("易错点最多三条", instructions)

    def test_repairs_unambiguous_latex_escape_fragments(self):
        payload = self.payload()
        payload["student_solution"] = payload["student_solution"].replace(
            "建立方程并求解。",
            "$$\\begin{aligned}v&=v_0\\\\\notag\n&=2v_0\\end{aligned}$$\n\n"
            "aqquad gqquad\n\n$$rac{mv^2}{2}=rac{q^2}{r}+rac34"
            "+\x1b[0m\\dfrac12+\\tfrac34+\x0crac12+\tfrac34$$",
        )

        normalized = analysis_artifacts.normalize_payload(payload)

        self.assertNotIn("otag", normalized["student_solution"])
        self.assertNotIn("aqquad", normalized["student_solution"])
        self.assertNotIn("gqquad", normalized["student_solution"])
        self.assertEqual(normalized["student_solution"].count(r"\qquad"), 2)
        self.assertNotIn("rac{", normalized["student_solution"].replace(r"\frac{", ""))
        self.assertNotRegex(normalized["student_solution"], r"(?<![\\A-Za-z])rac(?=[{\d])")
        self.assertIn(r"\frac34", normalized["student_solution"])
        self.assertNotIn("\x1b", normalized["student_solution"])
        self.assertNotIn(r"\dfrac", normalized["student_solution"])
        self.assertNotIn(r"\tfrac", normalized["student_solution"])
        self.assertEqual(normalized["student_solution"].count(r"\frac"), 7)

    def test_decisive_relation_allows_a_reproducible_compound_equation(self):
        payload = self.payload()
        relation = "；".join(["分段状态递推并核对边界条件"] * 10)
        self.assertGreater(len(relation), 120)
        payload["method_check"]["decisive_relations"] = [relation]

        normalized = analysis_artifacts.normalize_payload(payload)

        self.assertEqual(normalized["method_check"]["decisive_relations"], [relation])

    def test_rejects_non_ansi_control_characters(self):
        payload = self.payload()
        payload["student_solution"] += "\n\n异常控制符：\x01"

        with self.assertRaisesRegex(ValueError, "unsupported control characters"):
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

    def test_olympiad_profile_allows_calculus_but_not_university_mechanics(self):
        calculus = self.payload()["student_solution"].replace(
            "建立方程并求解。",
            "使用积分 $W=\\int F\\,dx$ 求解。",
        )
        self.assertFalse(analysis_artifacts.student_method_errors(calculus, "olympiad_official"))
        lagrange = calculus.replace("使用积分", "使用拉格朗日方程与积分")
        self.assertTrue(
            any(
                "大学力学方法" in item
                for item in analysis_artifacts.student_method_errors(lagrange, "olympiad_official")
            )
        )

    def test_rejects_method_check_step_count_mismatch(self):
        payload = self.payload()
        payload["method_check"]["student_step_count"] = 2
        with self.assertRaisesRegex(ValueError, "does not match"):
            analysis_artifacts.normalize_payload(payload)

    def test_rejects_reasoning_step_count_mismatch(self):
        payload = self.payload()
        payload["method_check"]["reasoning_steps"].append("多余步骤")
        with self.assertRaisesRegex(ValueError, "reasoning_steps does not match"):
            analysis_artifacts.normalize_payload(payload)

    def test_existing_physics_model_blocks_wrong_options_and_preserves_physical_svg(self):
        (self.entry / "physics-model.json").write_text(
            json.dumps(
                {
                    "student_solution": {
                        "quick_answers": ["A 错；B、C、D 正确"],
                    }
                },
                ensure_ascii=False,
            ),
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
