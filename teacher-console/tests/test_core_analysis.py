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


if __name__ == "__main__":
    unittest.main()
