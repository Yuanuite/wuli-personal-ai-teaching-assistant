import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "teacher-console" / "scripts" / "paired_answer_benchmark.py"
SPEC = importlib.util.spec_from_file_location("paired_answer_benchmark_test", SCRIPT)
assert SPEC is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class PairedAnswerBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        self.entry = self.library / "entries" / "entry-1"
        self.entry.mkdir(parents=True)
        (self.entry / "problem.md").write_text("# 题目\nA. 甲\nB. 乙", encoding="utf-8")
        reference = "# 解析\n\n## 答案速览\nA 错；B 对。\n\n$$v=at$$\n"
        (self.entry / "student-solution.md").write_text(reference, encoding="utf-8")
        (self.entry / "record.json").write_text(json.dumps({
            "title": "测试题",
            "answer_review": {"status": "passed"},
            "difficulty_assessment": {"score": 48, "level": "中等"},
        }, ensure_ascii=False), encoding="utf-8")
        baseline = self.entry / ".agent-baseline"
        baseline.mkdir()
        (baseline / "student-solution.md").write_text(
            "# 解析\n\n## 答案速览\nA 对；B 错。\n\n$$v=at$$\n",
            encoding="utf-8",
        )
        self.experiment = Path(self.temp.name) / "experiment"

    def tearDown(self):
        self.temp.cleanup()

    def test_seed_capture_and_compare_against_teacher_truth(self):
        manifest = benchmark.seed(self.library, self.experiment, 1)
        self.assertEqual(len(manifest["cases"]), 1)
        self.assertTrue((self.experiment / "prompts" / "entry-1.txt").is_file())
        self.assertEqual(
            benchmark.capture_web(self.library, self.experiment),
            {"captured": 1, "missing": 0},
        )
        imported = benchmark.evaluate(self.library, self.experiment)
        self.assertEqual(imported["readiness"]["missing_web_current"], 1)
        web_meta = self.experiment / "artifacts" / "entry-1" / "web-current.meta.json"
        web_meta.write_text(json.dumps({
            "schema_version": 1,
            "entry_id": "entry-1",
            "cohort": "web-current",
            "source": "teacher-console-browser-click",
            "status": "completed",
            "evidence_mode": "current",
            "evidence_context": {"status": "ready", "reference_count": 2},
        }), encoding="utf-8")
        no_rag = self.experiment / "artifacts" / "entry-1" / "web-no-rag.md"
        no_rag.write_text("# 无 RAG\nA 错；B 对。\n\n$$v=at$$\n", encoding="utf-8")
        no_rag.with_suffix(".meta.json").write_text(json.dumps({
            "schema_version": 1,
            "entry_id": "entry-1",
            "cohort": "web-no-rag",
            "source": "teacher-console-browser-click",
            "status": "completed",
            "evidence_mode": "disabled",
            "evidence_context": {
                "status": "disabled-for-benchmark",
                "reference_count": 0,
            },
        }), encoding="utf-8")
        direct = self.experiment / "artifacts" / "entry-1" / "direct.md"
        direct.parent.mkdir(parents=True, exist_ok=True)
        direct.write_text(
            "# 直接答案\nA 错；B 对。\n\n$$v=at$$\n",
            encoding="utf-8",
        )
        report = benchmark.evaluate(self.library, self.experiment)
        by_cohort = {item["cohort"]: item for item in report["records"]}
        self.assertTrue(by_cohort["direct"]["metrics"]["option_verdict_match"])
        self.assertFalse(by_cohort["web-current"]["metrics"]["option_verdict_match"])
        self.assertTrue(by_cohort["web-no-rag"]["metrics"]["option_verdict_match"])
        self.assertEqual(
            by_cohort["web-current"]["generation"]["evidence_reference_count"],
            2,
        )
        self.assertTrue(report["direct_is_reference_baseline_only"])
        self.assertEqual(report["readiness"]["pair_ready_count"], 1)
        self.assertEqual(report["readiness"]["three_way_ready_count"], 1)
        self.assertEqual(report["readiness"]["comparison_ready_count"], 0)

    def test_refresh_references_uses_only_teacher_passed_answer(self):
        benchmark.seed(self.library, self.experiment, 1)
        (self.entry / "student-solution.md").write_text("教师最终稿", encoding="utf-8")
        result = benchmark.refresh_references(self.library, self.experiment)
        self.assertEqual(result, {"refreshed": 1, "skipped": 0})
        manifest = json.loads((self.experiment / "manifest.json").read_text())
        self.assertEqual(
            manifest["cases"][0]["reference_digest"],
            benchmark.digest_text("教师最终稿"),
        )

    def test_legacy_zero_evidence_web_output_cannot_masquerade_as_current_rag(self):
        benchmark.seed(self.library, self.experiment, 1)
        artifact = self.experiment / "artifacts" / "entry-1"
        artifact.mkdir(parents=True, exist_ok=True)
        (artifact / "web.md").write_text("旧网页候选", encoding="utf-8")
        (artifact / "web.meta.json").write_text(json.dumps({
            "source": "teacher-console-browser-click",
            "status": "completed",
            "evidence_context": {"status": "ready", "reference_count": 0},
        }), encoding="utf-8")

        report = benchmark.evaluate(self.library, self.experiment)

        self.assertFalse(report["readiness"]["cases"][0]["web_current"])
        self.assertEqual(report["readiness"]["missing_web_current"], 1)


if __name__ == "__main__":
    unittest.main()
