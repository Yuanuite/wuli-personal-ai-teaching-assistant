"""Unit tests for the deterministic teaching-method gate.

Regression (2026-08-04 incident): a correct high-school candidate for the
coaxial-cylinder problem was rejected because the bare-substring rule flagged
pedagogical prose — a definitional tip ("冲量矩是力矩对时间的积分") and
negation guidance — even though the solution applies no calculus. The gate
must reject applied methods while passing definitions, tips and negations.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

CONSOLE = Path(__file__).resolve().parents[1]
if str(CONSOLE) not in sys.path:
    sys.path.insert(0, str(CONSOLE))

from teaching_method_policy import (  # noqa: E402
    HIGH_SCHOOL_STANDARD,
    OLYMPIAD_OFFICIAL,
    method_errors,
)

# Exact sentences from the rejected 2026-08-04 candidate.
INCIDENT_TIP = "冲量矩是力矩对时间的积分，等于角动量变化量，不要与功混淆。"


class AppliedMethodsRejectedTest(unittest.TestCase):
    def test_latex_integral_is_rejected(self):
        self.assertTrue(method_errors("由 $\\int_0^t v\\,dt$ 得位移。"))
        self.assertTrue(method_errors("使用积分 $W=\\int F\\,dx$ 计算。"))

    def test_unicode_integral_symbol_is_rejected(self):
        self.assertTrue(method_errors("由 ∫₀ᵗ v dt 可得位移。"))

    def test_int_prefix_commands_are_not_false_positives(self):
        self.assertFalse(method_errors("\\intercal 符号与积分运算无关。"))

    def test_operative_prose_is_rejected(self):
        self.assertTrue(method_errors("对位移积分可得做功。"))
        self.assertTrue(method_errors("第 3 步通过对时间积分完成推导。"))
        self.assertTrue(method_errors("对速度求导得到加速度。"))

    def test_bare_latex_integral_without_prose_is_rejected(self):
        # The legacy \\int\\b anchor missed \\int_0 because '_' is a word
        # character; the gate must not depend on the prose word 积分.
        self.assertTrue(method_errors("$x=\\int_0^t v\\,\\mathrm{d}t$。"))


class PedagogicalMentionsPassTest(unittest.TestCase):
    def test_incident_tip_sentence_passes(self):
        self.assertFalse(method_errors(INCIDENT_TIP))

    def test_definitional_mention_passes(self):
        self.assertFalse(method_errors("加速度是速度对时间的导数。"))
        self.assertFalse(method_errors("导数是描述变化率的工具。"))

    def test_negation_guidance_passes(self):
        self.assertFalse(method_errors("无需积分，用动能定理即可。"))
        self.assertFalse(method_errors("避免使用毕奥-萨伐尔定律直接积分。"))
        self.assertFalse(method_errors("不要使用导数，改用动能定理。"))

    def test_negation_before_same_sentence_still_rejects_genuine_use(self):
        text = "无需积分即可求解；但第 3 步通过对时间积分完成推导。"
        self.assertTrue(method_errors(text))


class ProfileBehaviorTest(unittest.TestCase):
    def test_olympiad_profile_permits_calculus(self):
        self.assertFalse(method_errors("使用积分 $W=\\int F\\,dx$ 计算。", OLYMPIAD_OFFICIAL))

    def test_olympiad_profile_still_forbids_university_mechanics(self):
        errors = method_errors("用拉格朗日量列式求解。", OLYMPIAD_OFFICIAL)
        self.assertTrue(any("拉格朗日" in error or "大学力学" in error for error in errors))

    def test_high_school_profile_labels_integration(self):
        errors = method_errors("对位移积分可得做功。", HIGH_SCHOOL_STANDARD)
        self.assertEqual(errors, ["student_solution uses non-high-school method: 积分"])


if __name__ == "__main__":
    unittest.main()
