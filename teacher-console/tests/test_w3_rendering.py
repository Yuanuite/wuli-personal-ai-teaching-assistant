import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

import w3_rendering  # noqa: E402
import w3r_contract  # noqa: E402

FIXTURE = CONSOLE / "tests" / "fixtures" / "w3r" / "verified-multi-target.json"
CONDITION_FIXTURE = CONSOLE / "tests" / "fixtures" / "w3r" / "verified-condition-matrix.json"


def brief():
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return w3r_contract.build_w3r_brief(source["problem"], source["blueprint"], source["proof_package"])["brief"]


class W3RenderingTest(unittest.TestCase):
    def test_detailed_derivation_is_rendered_in_dedicated_section(self):
        value = brief()
        result = w3_rendering.render_w3r(value)
        student = result["student_solution_md"]
        # 验证 detailed_derivation 内容出现在 ## 分阶段详细推导 区域
        self.assertIn("## 分阶段详细推导", student)
        self.assertIn("由洛伦兹力提供向心力", student)
        self.assertIn("$$qvB = \\frac{mv^2}{R}$$", student)
        # 验证 detailed_derivation 中的公式不在违规列表中
        self.assertEqual(result["render_gate_report"]["status"], "pass")

    def test_detailed_derivation_formulas_are_not_flagged(self):
        value = brief()
        # 在 proof_skeleton 步骤的 detailed_derivation 中放入不在 formula_latex 中的公式
        for step in value["proof_skeleton"]:
            step["detailed_derivation"] = "推导过程：\n\n$$E=mc^2$$\n\n得证。"
        result = w3_rendering.render_w3r(value)
        # detailed_derivation 中的公式不应触发 formula-without-brief-source
        self.assertEqual(result["render_gate_report"]["status"], "pass")
        self.assertIn("$$E=mc^2$$", result["student_solution_md"])

    def test_brief_without_detailed_derivation_falls_back_gracefully(self):
        import copy
        value = brief()
        # 移除所有 proof_skeleton 步骤中的 detailed_derivation
        for step in value["proof_skeleton"]:
            step.pop("detailed_derivation", None)
        result = w3_rendering.render_w3r(value)
        self.assertEqual(result["render_gate_report"]["status"], "pass")
        student = result["student_solution_md"]
        # 验证没有 detailed_derivation 时退回到 statement + formula
        self.assertIn("## 分阶段详细推导", student)
        self.assertIn("粒子在匀强磁场中做匀速圆周运动", student)
        self.assertIn("$$qvB=\\frac{mv^2}{R}$$", student)

    def test_renderer_outputs_student_teacher_and_passing_gate(self):
        value = brief()
        result = w3_rendering.render_w3r(value)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["render_gate_report"]["status"], "pass")
        for section in w3_rendering.REQUIRED_STUDENT_SECTIONS:
            self.assertIn(f"## {section}", result["student_solution_md"])
        self.assertNotIn("## 详细解答", result["student_solution_md"])
        self.assertIn("教师审计（不公开）", result["teacher_solution_md"])
        self.assertNotIn("教师审计", result["student_solution_md"])
        self.assertTrue(result["claim_span_map"])
        self.assertEqual(
            set(result["render_gate_report"]["metrics"].values()),
            {0.0, 1.0},
        )
        self.assertEqual(result["render_gate_report"]["metrics"]["unsupported_claim_rate"], 0.0)

    def test_latex_is_rendered_and_balanced(self):
        result = w3_rendering.render_w3r(brief())
        self.assertIn(r"\frac{mv^2}{R}", result["student_solution_md"])
        self.assertEqual(result["student_solution_md"].count("$$") % 2, 0)
        self.assertEqual(result["render_gate_report"]["metrics"]["latex_validity"], 1.0)

    def test_final_answer_drift_is_rejected_without_retry(self):
        value = brief()
        result = w3_rendering.render_w3r(value)
        result["student_solution_md"] = result["student_solution_md"].replace(r"t=\pi m/(qB)", r"t=2\pi m/(qB)")
        report = w3_rendering.render_gate(value, result)
        self.assertEqual(report["status"], "reject")
        self.assertTrue(any(item["code"] == "final-answer-drift" for item in report["violations"]))

    def test_condition_omission_is_rejected(self):
        value = brief()
        result = w3_rendering.render_w3r(value)
        result["student_solution_md"] = result["student_solution_md"].replace("向右为正", "方向约定已知")
        report = w3_rendering.render_gate(value, result)
        self.assertEqual(report["status"], "reject")
        self.assertTrue(any(item["code"] == "condition-omitted" for item in report["violations"]))

    def test_unknown_claim_in_span_is_rejected(self):
        value = brief()
        result = w3_rendering.render_w3r(value)
        result["claim_span_map"][0]["claim_ids"] = ["invented-claim"]
        report = w3_rendering.render_gate(value, result)
        self.assertEqual(report["status"], "reject")
        self.assertTrue(any(item["code"] == "unsupported-claim" for item in report["violations"]))

    def test_missing_section_is_retryable(self):
        value = brief()
        result = w3_rendering.render_w3r(value)
        result["student_solution_md"] = result["student_solution_md"].replace("## 建模与符号", "## 符号")
        report = w3_rendering.render_gate(value, result)
        self.assertEqual(report["status"], "retryable")

    def test_retry_uses_identical_brief_fingerprint(self):
        value = brief()
        seen = []

        def renderer(frozen, attempt):
            seen.append(w3r_contract.brief_fingerprint(frozen))
            result = w3_rendering.render_w3r(frozen, attempt=attempt)
            if attempt == 1:
                result["student_solution_md"] = result["student_solution_md"].replace("## 建模与符号", "## 符号")
            return result

        result = w3_rendering.render_with_single_retry(value, renderer)
        self.assertEqual(result["attempt"], 2)
        self.assertEqual(seen, [seen[0], seen[0]])
        self.assertEqual(result["render_gate_report"]["status"], "pass")

    def test_retry_refuses_changed_brief_binding(self):
        value = brief()

        def renderer(frozen, attempt):
            result = w3_rendering.render_w3r(frozen, attempt=attempt)
            result["brief_fingerprint"] = "sha256:" + "0" * 64
            return result

        with self.assertRaisesRegex(ValueError, "frozen Brief"):
            w3_rendering.render_with_single_retry(value, renderer)

    def test_insufficient_steps_never_trigger_solver_fallback(self):
        value = brief()
        value["proof_skeleton"] = [item for item in value["proof_skeleton"] if item["target_ids"] != ["q2"]]
        result = w3_rendering.render_w3r(value)
        self.assertEqual(result["status"], "needs_render_material")
        self.assertEqual(result["student_solution_md"], "")

    def test_render_result_contract_rejects_unknown_fields(self):
        result = w3_rendering.render_w3r(brief())
        result["solver_request"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            w3r_contract.normalize_render_result(result)

    def test_physics_condition_matrix_is_retained_verbatim(self):
        source = json.loads(CONDITION_FIXTURE.read_text(encoding="utf-8"))
        value = w3r_contract.build_w3r_brief(source["problem"], source["blueprint"], source["proof_package"])["brief"]
        result = w3_rendering.render_w3r(value)
        for condition in (
            "不能外推为任意全过程关系",
            "采用地面参考系，向上为正",
            "x=0 为第一段与第二段的接口",
            "首次进入第二段",
            "速度单位为 m/s，量纲为 L/T",
            "当 s→0 时 v→0",
        ):
            self.assertIn(condition, result["student_solution_md"])
        self.assertEqual(result["render_gate_report"]["metrics"]["condition_retention"], 1.0)

    def test_formula_without_brief_source_is_rejected(self):
        value = brief()
        result = w3_rendering.render_w3r(value)
        result["student_solution_md"] += "\n$$E=mc^2$$\n"
        report = w3_rendering.render_gate(value, result)
        self.assertEqual(report["status"], "reject")
        self.assertTrue(any(item["code"] == "formula-without-brief-source" for item in report["violations"]))

    def test_span_fingerprint_must_point_to_rendered_text(self):
        value = brief()
        result = w3_rendering.render_w3r(value)
        result["claim_span_map"][0]["text_fingerprint"] = "sha256:" + "0" * 64
        report = w3_rendering.render_gate(value, result)
        self.assertEqual(report["status"], "reject")
        self.assertTrue(any(item["code"] == "claim-span-text-mismatch" for item in report["violations"]))


if __name__ == "__main__":
    unittest.main()
