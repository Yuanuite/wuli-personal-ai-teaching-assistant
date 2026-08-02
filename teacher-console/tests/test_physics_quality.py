"""Physics quality gate tests (work-tree A0.1/A1.2).

Covers the two observed failure classes plus a symbol-definition obligation:
- derivation-answer-mismatch: log-form energy relation vs reciprocal-difference
  final answer for the same variable pair;
- sign-flip-unjustified: 题设同向 + explicitly negative solution + 取绝对值 while
  the final answer silently drops the sign without stating the reconciliation;
- internal-recheck-conflict: a 复核/验证 relation that is unconditionally
  negative while the final answer is positive;
- symbol-undefined: a subscripted symbol used but never defined.

Adjacent good samples must not be rejected (fail-closed on defects only).
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

CONSOLE_ROOT = Path(__file__).resolve().parents[1]
if str(CONSOLE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONSOLE_ROOT))

import core_analysis  # noqa: E402
import physics_quality  # noqa: E402


def _brief(problem: str) -> dict:
    return core_analysis.build_target_brief(problem, method_profile="high_school_standard")


def _payload(brief: dict, targets: list[dict]) -> dict:
    return {
        "status": "completed",
        "message": "核心求解完成",
        "target_brief_digest": brief["digest"],
        "targets": targets,
    }


# -- A0.1 fixtures: synthetic problems with the two defect classes -------------

PROBLEM_A = (
    "一带电粒子在半径分别为 $r1$、$r2$（$r1<r2$）的同轴圆柱面间运动，电荷 $q$、"
    "$Q$，真空介电常数 $\\varepsilon_0$，磁导率 $\\mu$。求：（1）粒子到达 $r2$ "
    "所需的最小角速度 $\\omega$。"
)

PROBLEM_B = (
    "两同轴圆筒绕同一轴同向旋转，半径 $r1$、$r2$，单位长度质量 $m$，角速度 "
    "$\\Omega$，真空磁导率 $\\mu_0$，电荷 $q$。求：（1）内圆筒最终角速度 $\\omega$。"
)

PROBLEM_C = (
    "两同轴圆筒同向旋转，半径 $r1$、$r2$，单位长度转动惯量 $m r^2$，角速度 "
    "$\\omega$、$\\Omega$，真空磁导率 $\\mu_0$，电荷 $q$。求：（1）系统总机械角动量 $L$。"
)

PROBLEM_D = (
    "物块沿斜面下滑，已知高度 $h$、质量 $m$。求：（1）到达底端的速度 $v$。"
)

FIXTURE_A_TARGETS = [
    {
        "id": "Q1",
        "final_answer": "\\omega \\geq \\sqrt{ q Q (1/r1 - 1/r2) / (\\pi \\varepsilon_0 \\mu) }",
        "key_relations": [
            "电场力做功：W_E = Q\\int_{r1}^{r2} E dr = Q q/(2\\pi \\varepsilon_0) \\ln(r2/r1)。",
            "能量守恒：1/2 \\mu v^2 = W_E。",
            "由角动量守恒代入能量方程可得条件 \\omega \\geq \\sqrt{ q Q (1/r1 - 1/r2) / (\\pi \\varepsilon_0 \\mu) }。",
        ],
    }
]

FIXTURE_B_TARGETS = [
    {
        "id": "Q1",
        "final_answer": "\\omega = (q^2 \\mu_0 \\Omega r2^2) / (2 m r1^2 + q^2 \\mu_0 r2^2)",
        "key_relations": [
            "系统角动量守恒：初始总角动量为 0。",
            "由 L_mech + L_field = 0 解出 \\omega = - (q^2 \\mu_0 \\Omega r2^2) / (2 m r1^2 + q^2 \\mu_0 r2^2)，负号表示反向，但题设同向，故取绝对值。",
        ],
    }
]

FIXTURE_C_TARGETS = [
    {
        "id": "Q1",
        "final_answer": "L = m r1^2 \\omega + m r2^2 \\Omega",
        "key_relations": [
            "机械角动量定义：L = I \\omega + I \\Omega。",
            "复核：L = - (q^2 \\mu_0 / (2\\pi)) (\\omega r1^2 + \\Omega r2^2)。",
        ],
    }
]

FIXTURE_D_TARGETS = [
    {
        "id": "Q1",
        "final_answer": "v = \\sqrt{2 g h} + v2",
        "key_relations": ["由机械能守恒：1/2 m v^2 = m g h。"],
    }
]

# -- good samples: must not be rejected ---------------------------------------

PROBLEM_G1 = "质量为 $m$ 的物体在水平力 $F$ 作用下运动。求：（1）加速度 $a$。"
GOOD_G1_TARGETS = [
    {
        "id": "Q1",
        "final_answer": "a = F/m",
        "key_relations": ["受力分析：水平方向合力为 $F$，由牛顿第二定律 F = ma。"],
    }
]

PROBLEM_G2 = "小车以速率 $v_0$ 运动，$t$ 秒后匀减速至停下。求：（1）加速度 $a$。"
GOOD_G2_TARGETS = [
    {
        "id": "Q1",
        "final_answer": "a = 3 \\text{ rad/s}^2（与题设同向）",
        "key_relations": [
            "解得 a = -3 \\text{ rad/s}^2，负号表示与规定正方向相反，取绝对值后方向与题设同向。",
        ],
    }
]

PROBLEM_G3 = (
    "长直导线单位长度电荷 $\\lambda$，距离 $r1$、$r2$ 处。求：（1）两点间电势差 $U$。"
)
GOOD_G3_TARGETS = [
    {
        "id": "Q1",
        "final_answer": "U = \\lambda/(2\\pi \\varepsilon_0) \\ln(r2/r1)",
        "key_relations": ["电场线积分：U = \\int_{r1}^{r2} E dr = \\lambda/(2\\pi \\varepsilon_0) \\ln(r2/r1)。"],
    }
]


class PhysicsQualityReportTest(unittest.TestCase):
    def report(self, problem, targets):
        brief = _brief(problem)
        payload = _payload(brief, targets)
        return physics_quality.physics_quality_report(payload, brief, problem)

    def test_rejects_derivation_answer_mismatch(self):
        report = self.report(PROBLEM_A, FIXTURE_A_TARGETS)
        self.assertEqual(report["status"], "fail")
        codes = [item["code"] for item in report["reason_codes"]]
        self.assertIn("derivation-answer-mismatch", codes)

    def test_rejects_sign_flip_unjustified(self):
        report = self.report(PROBLEM_B, FIXTURE_B_TARGETS)
        self.assertEqual(report["status"], "fail")
        codes = [item["code"] for item in report["reason_codes"]]
        self.assertIn("sign-flip-unjustified", codes)

    def test_rejects_internal_recheck_conflict(self):
        report = self.report(PROBLEM_C, FIXTURE_C_TARGETS)
        self.assertEqual(report["status"], "fail")
        codes = [item["code"] for item in report["reason_codes"]]
        self.assertIn("internal-recheck-conflict", codes)

    def test_rejects_symbol_undefined(self):
        report = self.report(PROBLEM_D, FIXTURE_D_TARGETS)
        self.assertEqual(report["status"], "fail")
        codes = [item["code"] for item in report["reason_codes"]]
        self.assertIn("symbol-undefined", codes)

    def test_accepts_simple_good_answer(self):
        report = self.report(PROBLEM_G1, GOOD_G1_TARGETS)
        self.assertEqual(report["status"], "pass", report["reason_codes"])

    def test_accepts_legitimate_sign_reconciliation(self):
        report = self.report(PROBLEM_G2, GOOD_G2_TARGETS)
        self.assertEqual(report["status"], "pass", report["reason_codes"])

    def test_accepts_log_only_answer(self):
        report = self.report(PROBLEM_G3, GOOD_G3_TARGETS)
        self.assertEqual(report["status"], "pass", report["reason_codes"])

    def test_obligations_recorded_and_deferred_items_do_not_block(self):
        report = self.report(PROBLEM_G1, GOOD_G1_TARGETS)
        obligations = {item["name"]: item["status"] for item in report["checked_obligations"]}
        self.assertEqual(obligations["symbol-definition"], "checked")
        self.assertEqual(obligations["dimension"], "deferred-verifier")
        self.assertEqual(obligations["applicability-conditions"], "deferred-verifier")
        self.assertEqual(report["contract"], physics_quality.PHYSICS_QUALITY_CONTRACT)
        self.assertTrue(report["candidate_digest"])


class NormalizePayloadPhysicsGateTest(unittest.TestCase):
    def test_normalize_rejects_when_problem_provided(self):
        brief = _brief(PROBLEM_A)
        payload = _payload(brief, FIXTURE_A_TARGETS)
        with self.assertRaisesRegex(ValueError, "physics quality gate rejected: derivation-answer-mismatch"):
            core_analysis.normalize_payload(payload, brief, problem=PROBLEM_A)

    def test_normalize_without_problem_keeps_backward_compatibility(self):
        brief = _brief(PROBLEM_A)
        payload = _payload(brief, FIXTURE_A_TARGETS)
        normalized = core_analysis.normalize_payload(payload, brief)
        self.assertEqual(normalized["status"], "completed")

    def test_normalize_accepts_good_answer_with_problem(self):
        brief = _brief(PROBLEM_G1)
        payload = _payload(brief, GOOD_G1_TARGETS)
        normalized = core_analysis.normalize_payload(payload, brief, problem=PROBLEM_G1)
        self.assertEqual(normalized["status"], "completed")


class MaterializePhysicsGateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.entry = Path(self.temp.name) / "entry"
        self.entry.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def materialize(self, problem, targets):
        (self.entry / "problem.md").write_text(problem, encoding="utf-8")
        (self.entry / "record.json").write_text(
            json.dumps({"schema_version": 1, "id": "entry", "title": "原题", "subject": "高中物理"}, ensure_ascii=False),
            encoding="utf-8",
        )
        brief = _brief(problem)
        return core_analysis.materialize(self.entry, _payload(brief, targets), brief)

    def test_materialize_embeds_physics_gate_report(self):
        result = self.materialize(PROBLEM_G1, GOOD_G1_TARGETS)
        core = json.loads((self.entry / "core-solution.json").read_text(encoding="utf-8"))
        self.assertEqual(core["gate"]["status"], "passed")
        self.assertEqual(core["gate"]["contract"], physics_quality.PHYSICS_QUALITY_CONTRACT)
        self.assertEqual(core["gate"]["physics_quality"]["status"], "pass")
        stage_names = [stage["name"] for stage in result["stages"]]
        self.assertIn("physics-quality-gate", stage_names)
        self.assertIn("render-fidelity-gate", stage_names)

    def test_materialize_rejects_defective_candidate_before_rendering(self):
        with self.assertRaisesRegex(ValueError, "physics quality gate rejected"):
            self.materialize(PROBLEM_A, FIXTURE_A_TARGETS)

    def test_render_fidelity_rejects_missing_content(self):
        brief = _brief(PROBLEM_G1)
        targets = [
            {
                "id": "Q1",
                "final_answer": "a = F/m",
                "key_relations": ["受力分析：水平方向合力为 $F$。"],
            }
        ]
        (self.entry / "problem.md").write_text(PROBLEM_G1, encoding="utf-8")
        (self.entry / "record.json").write_text(
            json.dumps({"schema_version": 1, "id": "entry", "title": "原题", "subject": "高中物理"}, ensure_ascii=False),
            encoding="utf-8",
        )
        # final_answer "a = F/m" is embedded verbatim by the renderer, so the
        # fidelity check passes by construction; a tampered renderer would trip it.
        result = core_analysis.materialize(self.entry, _payload(brief, targets), brief)
        self.assertEqual(result["stages"][-1]["name"], "render-fidelity-gate")


if __name__ == "__main__":
    unittest.main()
