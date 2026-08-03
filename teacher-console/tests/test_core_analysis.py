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
        with self.assertRaisesRegex(ValueError, "target_brief_digest"):            core_analysis.normalize_payload(payload, self.brief)

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


if __name__ == "__main__":
    unittest.main()
