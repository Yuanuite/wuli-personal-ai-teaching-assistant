import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "teacher-console" / "scripts" / "default_obligation_shadow_report.py"
SPEC = importlib.util.spec_from_file_location("default_obligation_shadow_report", SCRIPT)
assert SPEC is not None
module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(module)


class DefaultObligationShadowReportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "student-error-library"
        self.entry = self.library / "entries" / "entry-1"
        self.entry.mkdir(parents=True)
        (self.entry / "record.json").write_text(
            json.dumps({"title": "多次过界粒子"}),
            encoding="utf-8",
        )
        (self.entry / "problem.md").write_text(
            "粒子可多次穿过区域边界。",
            encoding="utf-8",
        )
        report = {
            "status": "completed",
            "entry_id": "entry-1",
            "report": {
                "blueprint": {
                    "question_targets": [
                        {
                            "id": "Q1",
                            "prompt": "求粒子进入区域的时刻",
                            "answer_type": "time",
                        }
                    ],
                    "verification_obligations": [
                        {
                            "id": "V1",
                            "target_id": "Q1",
                            "check": "复算进入时刻",
                            "risk": "medium",
                        }
                    ],
                }
            },
        }
        (self.entry / "w3-shadow-report.json").write_text(
            json.dumps(report, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_recomputes_shadow_suggestions_from_stored_blueprint(self):
        report = module.summarize(self.library, include_ipho=False)
        self.assertEqual(report["totals"]["w3_report_count"], 1)
        self.assertEqual(report["totals"]["triggered_case_count"], 1)
        self.assertEqual(report["totals"]["suggestion_count"], 1)
        suggestion = report["cases"][0]["suggestions"][0]
        self.assertEqual(
            suggestion["rule_id"],
            "default.solve.all-physical-solutions.v1",
        )
        self.assertFalse(report["safety"]["solver_affected"])
        self.assertEqual(
            report["safety"]["promotion_recommendation"],
            "do-not-promote-to-hard-gate-yet",
        )

    def test_markdown_marks_report_as_shadow_only(self):
        text = module.markdown(module.summarize(self.library, include_ipho=False))
        self.assertIn("只统计，不影响 Solver / VERIFIED / 交付", text)
        self.assertIn("多次过界粒子", text)


if __name__ == "__main__":
    unittest.main()
