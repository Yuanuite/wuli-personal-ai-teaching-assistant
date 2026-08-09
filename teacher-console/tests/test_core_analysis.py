import json
import sys
import tempfile
import unittest
from pathlib import Path

CONSOLE_ROOT = Path(__file__).resolve().parents[1]
if str(CONSOLE_ROOT) not in sys.path:
    sys.path.insert(0, str(CONSOLE_ROOT))

import core_analysis


class CoreAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.entry = Path(self.temp.name) / "entry"
        self.entry.mkdir()
        (self.entry / "record.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "entry",
                    "title": "原题",
                    "subject": "高中物理",
                    "knowledge_points": ["待整理"],
                    "error_types": ["待整理"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.brief = core_analysis.build_target_brief(
            "（1）求速度。\n（2）求所有可能的返回时刻。",
            method_profile="olympiad_official",
        )

    def tearDown(self):
        self.temp.cleanup()

    def payload(self):
        return {
            "status": "completed",
            "message": "核心求解完成",
            "target_brief_digest": self.brief["digest"],
            "targets": [
                {
                    "id": item["id"],
                    "final_answer": f"{item['id']} 的答案为 $2v_0$",
                    "key_relations": [
                        "由动量守恒与能量关系联立可得 $v=2v_0$",
                        "取满足题设方向的物理解并检查量纲",
                    ],
                }
                for item in self.brief["targets"]
            ],
        }

    def test_target_brief_is_small_bound_and_risk_driven(self):
        self.assertEqual([item["id"] for item in self.brief["targets"]], ["Q1", "Q2"])
        self.assertIn("multiple-targets", self.brief["risk_signals"])
        self.assertIn("boundary-or-branch", self.brief["risk_signals"])
        self.assertTrue(self.brief["enhancements"]["independent_verification"])

    def test_materialize_keeps_core_and_render_fidelity(self):
        result = core_analysis.materialize(self.entry, self.payload(), self.brief)
        self.assertEqual(result["contract"], core_analysis.CORE_CONTRACT)
        core = json.loads((self.entry / "core-solution.json").read_text(encoding="utf-8"))
        self.assertEqual(core["gate"]["status"], "passed")
        student = (self.entry / "student-solution.md").read_text(encoding="utf-8")
        teacher = (self.entry / "teacher-solution.md").read_text(encoding="utf-8")
        for target in self.payload()["targets"]:
            self.assertIn(target["final_answer"], student)
        self.assertIn("确定性 Core Gate", teacher)
        self.assertEqual(teacher, (self.entry / "solution.md").read_text(encoding="utf-8"))

    def test_rejects_stale_target_binding(self):
        payload = self.payload()
        payload["target_brief_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "target_brief_digest"):
            core_analysis.normalize_payload(payload, self.brief)

    def test_rejects_missing_target_instead_of_guessing_coverage(self):
        payload = self.payload()
        payload["targets"] = payload["targets"][:1]
        with self.assertRaisesRegex(ValueError, "Target Brief order"):
            core_analysis.normalize_payload(payload, self.brief)

    def test_missing_config_uses_core_first_default(self):
        config, errors = core_analysis.normalize_routing_config({})
        self.assertEqual(errors, [])
        self.assertEqual(config["mode"], "core-first")

    def test_legacy_rollback_is_explicit(self):
        config, errors = core_analysis.normalize_routing_config({
            "schema_version": 1,
            "policy_version": "wuli-core-first-routing-v1",
            "mode": "legacy-adaptive",
            "max_latency_seconds": 90,
        })
        self.assertEqual(errors, [])
        self.assertEqual(config["mode"], "legacy-adaptive")


class CoreCheckpointTest(unittest.TestCase):
    """Work-tree B1: gate-only failures must leave a zero-token replay checkpoint."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.entry = Path(self.temp.name) / "library" / "entries" / "entry"
        self.entry.mkdir(parents=True)
        self.problem = "物块沿斜面下滑，已知高度 $h$、质量 $m$。求：（1）到达底端的速度 $v$。"
        (self.entry / "problem.md").write_text(self.problem, encoding="utf-8")
        (self.entry / "record.json").write_text(
            json.dumps({"schema_version": 1, "id": "entry", "title": "原题"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.brief = core_analysis.build_target_brief(self.problem, method_profile="high_school_standard")

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, final_answer):
        return {
            "status": "completed",
            "message": "核心求解完成",
            "target_brief_digest": self.brief["digest"],
            "targets": [
                {
                    "id": "Q1",
                    "final_answer": final_answer,
                    "key_relations": ["由机械能守恒：1/2 m v^2 = m g h。"],
                }
            ],
        }

    def test_checkpoint_round_trip_requires_matching_fingerprint(self):
        core_analysis.save_checkpoint(
            self.entry, fingerprint="fp-1", payload=self.payload("v = \\sqrt{2 g h}"), brief=self.brief
        )
        loaded = core_analysis.load_checkpoint(self.entry, fingerprint="fp-1")
        assert loaded is not None
        self.assertEqual(loaded["targets"][0]["final_answer"], "v = \\sqrt{2 g h}")
        self.assertIsNone(core_analysis.load_checkpoint(self.entry, fingerprint="fp-other"))

    def test_gate_failure_still_saves_checkpoint_before_materialize(self):
        # ``v2`` is undefined in the problem, so the physics gate rejects at
        # materialization, but the structurally valid payload must survive for
        # a zero-token replay once the gate itself is repaired.
        rejected = self.payload("v = \\sqrt{2 g h} + v2")
        core_analysis.save_checkpoint(self.entry, fingerprint="fp-1", payload=rejected, brief=self.brief)
        with self.assertRaisesRegex(ValueError, "physics quality gate rejected"):
            core_analysis.materialize(self.entry, rejected, self.brief)
        self.assertIsNotNone(core_analysis.load_checkpoint(self.entry, fingerprint="fp-1"))

    def test_structurally_invalid_payload_cannot_become_checkpoint(self):
        bad = self.payload("v = \\sqrt{2 g h}")
        bad["target_brief_digest"] = "0" * 64
        with self.assertRaises(ValueError):
            core_analysis.save_checkpoint(self.entry, fingerprint="fp-1", payload=bad, brief=self.brief)

    def test_clear_checkpoint_drops_persisted_solve(self):
        core_analysis.save_checkpoint(
            self.entry, fingerprint="fp-1", payload=self.payload("v = \\sqrt{2 g h}"), brief=self.brief
        )
        core_analysis.clear_checkpoint(self.entry)
        self.assertIsNone(core_analysis.load_checkpoint(self.entry, fingerprint="fp-1"))
        core_analysis.clear_checkpoint(self.entry)  # idempotent

    def test_render_fidelity_rejection_detection(self):
        fidelity_failure = {
            "status": "failed",
            "attempts": [{"error": "render fidelity gate rejected: missing content: Q1: x"}],
        }
        self.assertTrue(core_analysis.render_fidelity_rejected(fidelity_failure))
        physics_failure = {
            "status": "failed",
            "attempts": [{"error": "physics quality gate rejected: symbol-undefined@Q1"}],
        }
        self.assertFalse(core_analysis.render_fidelity_rejected(physics_failure))
        self.assertFalse(core_analysis.render_fidelity_rejected({"status": "failed", "attempts": []}))
        self.assertFalse(core_analysis.render_fidelity_rejected({"status": "failed"}))


class CoreRichContractTest(unittest.TestCase):
    """Work-tree D1: complex problems get the rich five-section contract."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.entry = Path(self.temp.name) / "library" / "entries" / "entry"
        self.entry.mkdir(parents=True)
        self.problem = "物块从斜面顶端由静止下滑，第一次到达底端后进入水平面。求：（1）底端速度 $v$。"
        (self.entry / "problem.md").write_text(self.problem, encoding="utf-8")
        (self.entry / "record.json").write_text(
            json.dumps({"schema_version": 1, "id": "entry", "title": "原题"}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.brief = core_analysis.build_target_brief(self.problem, method_profile="high_school_standard")

    def tearDown(self):
        self.temp.cleanup()

    def rich_payload(self, student_solution=None):
        claims = [
            {
                "id": "Q1",
                "final_answer": "底端速度为 $v=\\sqrt{2gh}$",
                "key_relations": ["由机械能守恒：1/2 m v^2 = m g h。"],
            }
        ]
        if student_solution is None:
            student_solution = (
                "## 答案速览\n\n- **Q1**：底端速度为 $v=\\sqrt{2gh}$\n\n"
                "## 一眼识别\n\n最短主线是机械能守恒。\n\n"
                "## 详细解答\n\n### 第 1 步\n\n由机械能守恒：1/2 m v^2 = m g h。因此，底端速度为 $v=\\sqrt{2gh}$。\n\n"
                "## 易错点\n\n注意斜面光滑假设与题设边界。\n\n"
                "## 30 秒自测\n\n能否独立复算并检查适用条件？\n"
            )
        return {
            "status": "completed",
            "message": "rich 求解完成",
            "target_brief_digest": self.brief["digest"],
            "claims": claims,
            "student_solution": student_solution,
            "teacher_audit": "审计：机械能守恒适用于光滑斜面情形，已核对题设边界、方向与量纲，解法在高中范围内。",
            "method_check": {"selected_path": "机械能守恒直接求解"},
        }

    def test_rich_materialize_keeps_five_sections_and_claim_fidelity(self):
        payload = self.rich_payload()
        result = core_analysis.materialize(self.entry, payload, self.brief)
        self.assertEqual(result["contract"], core_analysis.CORE_RICH_CONTRACT)
        student = (self.entry / "student-solution.md").read_text(encoding="utf-8")
        for section in core_analysis.CORE_RICH_SECTIONS:
            self.assertIn(section, student)
        for claim in payload["claims"]:
            self.assertIn(claim["final_answer"], student)
            for relation in claim["key_relations"]:
                self.assertIn(relation, student)
        teacher = (self.entry / "teacher-solution.md").read_text(encoding="utf-8")
        self.assertIn("机械能守恒直接求解", json.loads((self.entry / "record.json").read_text(encoding="utf-8"))["standard_solution_path"]["selected_path"])  # noqa: E501
        self.assertIn("审计：机械能守恒", teacher)
        core = json.loads((self.entry / "core-solution.json").read_text(encoding="utf-8"))
        self.assertEqual(core["contract"], core_analysis.CORE_RICH_CONTRACT)

    def test_rich_contract_requires_explicit_derivation_basis(self):
        instructions = core_analysis.rich_output_contract(self.brief)["instructions"]
        self.assertIn("每条必须由题设或紧邻", instructions)
        self.assertIn("写明所依据的定律/守恒律", instructions)
        self.assertIn("代数上不一致的新公式", instructions)

    def test_compact_contract_requires_explicit_derivation_basis(self):
        instructions = core_analysis.output_contract(self.brief)["instructions"]
        self.assertIn("每条必须由题设或紧邻", instructions)
        self.assertIn("写明所依据的定律/守恒律", instructions)
        self.assertIn("代数上不一致的新公式", instructions)

    def test_rich_missing_section_is_rejected(self):
        long_filler = (
            "本题在光滑斜面假设下求解，物块从静止开始下滑，过程完整且足够长；"
            "此处仅缺少后续章节标题，用于验证章节完整性门禁，因此整段文本必须足够长才能通过长度检查。"
        )
        payload = self.rich_payload(
            student_solution="## 答案速览\n\n底端速度为 $v=\\sqrt{2gh}$，由机械能守恒得到。" + long_filler
        )
        with self.assertRaisesRegex(ValueError, "missing section"):
            core_analysis.normalize_payload(payload, self.brief)

    def test_rich_section_headings_tolerate_whitespace_drift(self):
        payload = self.rich_payload()
        # 模型把 "30 秒自测" 写成 "30秒自测"（去掉空格），折叠空白后应视为同一章节
        payload["student_solution"] = payload["student_solution"].replace("## 30 秒自测", "## 30秒自测")
        result = core_analysis.materialize(self.entry, payload, self.brief)
        self.assertEqual(result["contract"], core_analysis.CORE_RICH_CONTRACT)

    def test_rich_placeholder_is_rejected(self):
        payload = self.rich_payload()
        payload["student_solution"] = payload["student_solution"].replace("## 易错点", "## 易错点\n\nTODO")
        with self.assertRaisesRegex(ValueError, "placeholder"):
            core_analysis.normalize_payload(payload, self.brief)

    def test_rich_fidelity_rejects_missing_claim_content(self):
        payload = self.rich_payload()
        payload["student_solution"] = payload["student_solution"].replace("1/2 m v^2 = m g h。", "略。")
        with self.assertRaisesRegex(ValueError, "render fidelity gate rejected"):
            core_analysis.materialize(self.entry, payload, self.brief)

    def test_rich_fidelity_tolerates_whitespace_drift_around_inline_math(self):
        payload = self.rich_payload()
        # 模型在 Markdown 中对同一内容采用了不同的行内空格排版
        payload["student_solution"] = payload["student_solution"].replace(
            "由机械能守恒：1/2 m v^2 = m g h。", "由机械能守恒： 1/2  m  v^2  =  m  g  h 。"
        ).replace("底端速度为 $v=\\sqrt{2gh}$", "底端速度为  $v=\\sqrt{2gh}$ ")
        result = core_analysis.materialize(self.entry, payload, self.brief)
        self.assertEqual(result["contract"], core_analysis.CORE_RICH_CONTRACT)

    def test_rich_fidelity_rejects_reworded_claim_content(self):
        payload = self.rich_payload()
        # 改写而非空格差异：折叠空白后仍不匹配，必须拒绝
        payload["student_solution"] = payload["student_solution"].replace(
            "由机械能守恒：1/2 m v^2 = m g h。", "根据能量守恒：1/2 m v^2 = m g h。"
        )
        with self.assertRaisesRegex(ValueError, "render fidelity gate rejected"):
            core_analysis.materialize(self.entry, payload, self.brief)

    def test_fidelity_poisoned_checkpoint_must_not_survive(self):
        # Regression (2026-08-04 incident): the provider broke the
        # verbatim-claims contract; a checkpoint of that payload deadlocks
        # every zero-token replay because no gate repair can flip the verdict.
        payload = self.rich_payload()
        payload["student_solution"] = payload["student_solution"].replace("1/2 m v^2 = m g h。", "略。")
        core_analysis.save_checkpoint(self.entry, fingerprint="fp-1", payload=payload, brief=self.brief)
        gateway = {
            "status": "failed",
            "attempts": [{"error": "render fidelity gate rejected: missing content: Q1: 由机械能守恒"}],
        }
        self.assertTrue(core_analysis.render_fidelity_rejected(gateway))
        core_analysis.clear_checkpoint(self.entry)
        self.assertIsNone(core_analysis.load_checkpoint(self.entry, fingerprint="fp-1"))

    def test_complex_problem_enables_independent_verification(self):
        complex_problem = "粒子第一次进入磁场区域后恰好到达边界，求所有可能的磁感应强度。"
        brief = core_analysis.build_target_brief(complex_problem, method_profile="high_school_standard")
        self.assertTrue(brief["enhancements"]["independent_verification"])

    def test_olympiad_profile_accepts_calculus_in_claims(self):
        # Regression (2026-08-04 incident): problems whose natural solution
        # integrates must be regenerable under the olympiad profile instead of
        # looping forever under the high-school gate.
        brief = core_analysis.build_target_brief(self.problem, method_profile="olympiad_official")
        payload = self.rich_payload()
        payload["target_brief_digest"] = brief["digest"]
        payload["claims"][0]["key_relations"] = [
            "电场力做功：W_E = Q\\int_{r1}^{r2} E dr = Qq/(2\\pi \\varepsilon_0) \\ln(r2/r1)。",
        ]
        payload["student_solution"] = (
            "## 答案速览\n\n- **Q1**：底端速度为 $v=\\sqrt{2gh}$\n\n"
            "## 一眼识别\n\n最短主线是功能关系。\n\n"
            "## 详细解答\n\n### 第 1 步\n\n"
            "电场力做功：W_E = Q\\int_{r1}^{r2} E dr = Qq/(2\\pi \\varepsilon_0) \\ln(r2/r1)。"
            "因此，底端速度为 $v=\\sqrt{2gh}$。\n\n"
            "## 易错点\n\n注意题设边界。\n\n"
            "## 30 秒自测\n\n能否独立复算并检查适用条件？\n"
        )
        result = core_analysis.materialize(self.entry, payload, brief)
        self.assertEqual(result["contract"], core_analysis.CORE_RICH_CONTRACT)


if __name__ == "__main__":
    unittest.main()
