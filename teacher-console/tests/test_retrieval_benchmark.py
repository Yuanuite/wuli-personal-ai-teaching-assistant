import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kb  # noqa: E402
import knowledge_store  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "retrieval_benchmark", ROOT / "teacher-console" / "scripts" / "retrieval_benchmark.py"
)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


class RetrievalBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "momentum-collision"
        self.entry.mkdir(parents=True)
        kb.write_text(self.entry / "problem.md", "小车和木块发生完全非弹性碰撞，求共同速度。")
        kb.write_text(self.entry / "solution.md", "规定正方向，使用动量守恒，机械能不守恒。")
        kb.write_json(
            self.entry / "record.json",
            {
                "schema_version": 1,
                "id": self.entry.name,
                "title": "完全非弹性碰撞",
                "subject": "高中物理",
                "grade": "高二",
                "kind": "error",
                "status": "ready",
                "knowledge_points": ["动量守恒", "非弹性碰撞"],
                "error_types": ["误用机械能守恒"],
            },
        )
        knowledge_store.rebuild(self.library)

    def tearDown(self):
        self.temp.cleanup()

    def cases(self, count=1, status="approved"):
        categories = ("knowledge_point", "problem_type", "error_type", "teacher_phrase")
        return [
            {
                "schema_version": 1,
                "id": f"case-{index:03d}",
                "query": "动量守恒 非弹性碰撞",
                "category": categories[(index - 1) % len(categories)],
                "review_status": status,
                "relevant_entry_ids": [self.entry.name],
            }
            for index in range(1, count + 1)
        ]

    def test_validate_rejects_unknown_entry(self):
        cases = self.cases()
        cases[0]["relevant_entry_ids"] = ["missing"]
        result = benchmark.validate_cases(self.library, cases)
        self.assertFalse(result["valid"])
        self.assertIn("unknown relevant entries", result["errors"][0])

    def test_run_excludes_drafts_and_computes_recall(self):
        cases = self.cases()
        cases.append({**self.cases(status="draft")[0], "id": "draft-1"})
        report = benchmark.run_benchmark(self.library, cases, top_k=5)
        self.assertEqual(report["eligible_cases"], 1)
        self.assertEqual(report["excluded_cases"], 1)
        self.assertEqual(report["overall"]["recall"]["@5"], 1.0)
        self.assertFalse(report["fixed_set_ready"])

    def test_seed_is_draft_and_covers_all_categories(self):
        cases = benchmark.seed_cases(self.library, limit=4)
        self.assertEqual(len(cases), 4)
        self.assertEqual({case["category"] for case in cases}, benchmark.CATEGORIES)
        self.assertTrue(all(case["review_status"] == "draft" for case in cases))

    def test_recorded_approved_set_becomes_evolve_observation(self):
        report = benchmark.run_benchmark(self.library, self.cases(count=30), top_k=5)
        self.assertTrue(report["fixed_set_ready"])
        event = benchmark.record_report(self.library, report, {"case_count": 30, "top_k": 5})
        self.assertEqual(event["task_type"], "evolve.observation.retrieval")
        self.assertNotIn("per_case", event["result"])
        rebuilt = knowledge_store.rebuild(self.library)
        self.assertEqual(rebuilt["evolve_observations"], 1)
        evidence = knowledge_store.query(self.library, "动量守恒", mode="audit", top_k=2)
        self.assertEqual(evidence["evolve_observations"][0]["observation_type"], "evolve.observation.retrieval")


if __name__ == "__main__":
    unittest.main()
