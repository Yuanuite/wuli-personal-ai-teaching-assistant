import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT / "teacher-console" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import w3_shadow_benchmark


class W3ShadowBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "library"
        self.entries = self.library / "entries"
        self.entries.mkdir(parents=True)
        for index in range(6):
            entry = self.entries / f"entry-{index}"
            entry.mkdir()
            (entry / "student-solution.md").write_text(f"answer {index}", encoding="utf-8")
            (entry / "record.json").write_text(json.dumps({
                "title": f"题 {index}",
                "answer_review": {"status": "passed"},
                "difficulty_assessment": {"score": 50 + index},
            }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_seed_freezes_holdout_and_refuses_overwrite(self):
        experiment = self.root / "experiment"
        manifest = w3_shadow_benchmark.seed(self.library, experiment, holdout_count=5)
        holdout = [
            item for item in manifest["cases"] if item["evaluation_split"] == "holdout"
        ]
        self.assertEqual(len(holdout), 5)
        with self.assertRaises(FileExistsError):
            w3_shadow_benchmark.seed(self.library, experiment, holdout_count=5)

    def test_fresh_seed_excludes_every_prior_manifest_case(self):
        old_experiment = self.library / "evals" / "w3-shadow-v1"
        old_experiment.mkdir(parents=True)
        (old_experiment / "manifest.json").write_text(json.dumps({
            "kind": "w3-shadow-benchmark",
            "cases": [{"entry_id": "entry-5"}],
        }), encoding="utf-8")
        experiment = self.library / "evals" / "w3-shadow-w4-1"
        with self.assertRaisesRegex(ValueError, "found 5"):
            w3_shadow_benchmark.seed(
                self.library,
                experiment,
                holdout_count=6,
                fresh_only=True,
            )
        self.assertFalse(experiment.exists())
        manifest = w3_shadow_benchmark.seed(
            self.library,
            experiment,
            holdout_count=5,
            fresh_only=True,
            batch_id="batch-new",
        )
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["batch_id"], "batch-new")
        self.assertTrue(all(
            item["entry_id"] != "entry-5" for item in manifest["cases"]
        ))
        self.assertTrue(all(
            item["evaluation_split"] == "holdout" for item in manifest["cases"]
        ))

    def test_fresh_truth_must_be_frozen_before_scoring(self):
        experiment = self.root / "experiment"
        w3_shadow_benchmark.seed(
            self.library, experiment, holdout_count=5, fresh_only=True
        )
        result = w3_shadow_benchmark.score(self.library, experiment)
        self.assertFalse(result["gates"]["production_eligible"])
        self.assertIn(
            "fresh holdout: teacher truth must be frozen before scoring",
            result["validation"]["blocking_errors"],
        )

    def test_explicit_replay_cases_never_become_holdout(self):
        old_experiment = self.library / "evals" / "w3-shadow-v1"
        old_experiment.mkdir(parents=True)
        reused_ids = [f"entry-{index}" for index in range(5)]
        (old_experiment / "manifest.json").write_text(json.dumps({
            "kind": "w3-shadow-benchmark",
            "cases": [{"entry_id": entry_id} for entry_id in reused_ids],
        }), encoding="utf-8")
        experiment = self.library / "evals" / "w3-shadow-w4-replay"
        manifest = w3_shadow_benchmark.seed(
            self.library,
            experiment,
            holdout_count=5,
            fresh_only=True,
            reuse_case_ids=reused_ids,
        )
        self.assertEqual(
            {item["evaluation_split"] for item in manifest["cases"]},
            {"replay"},
        )
        self.assertEqual(manifest["independence"]["reused_case_count"], 5)
        result = w3_shadow_benchmark.score(self.library, experiment)
        self.assertFalse(result["gates"]["holdout_ready"])
        self.assertFalse(result["gates"]["production_eligible"])
        self.assertIsNone(result["replay"]["w2_target_accuracy"])

    def test_freeze_truth_locks_five_cases_and_twelve_targets(self):
        experiment = self.root / "experiment"
        manifest = w3_shadow_benchmark.seed(
            self.library, experiment, holdout_count=5, fresh_only=True
        )
        target_total = 0
        for case_index, case in enumerate(manifest["cases"]):
            target_count = 3 if case_index < 2 else 2
            target_total += target_count
            truth_path = experiment / "truth" / f"{case['entry_id']}.json"
            truth = json.loads(truth_path.read_text(encoding="utf-8"))
            truth["status"] = "approved"
            truth["targets"] = [
                {
                    "target_id": f"T{index + 1}",
                    "expected_conclusion": f"结论 {index + 1}",
                    "source_basis": "教师复核答案",
                }
                for index in range(target_count)
            ]
            truth_path.write_text(
                json.dumps(truth, ensure_ascii=False), encoding="utf-8"
            )
        self.assertEqual(target_total, 12)
        lock = w3_shadow_benchmark.freeze_truth(self.library, experiment)
        self.assertEqual(lock["case_count"], 5)
        self.assertEqual(lock["target_count"], 12)
        with self.assertRaises(FileExistsError):
            w3_shadow_benchmark.freeze_truth(self.library, experiment)

    def test_missing_labels_keep_production_gate_closed(self):
        experiment = self.root / "experiment"
        w3_shadow_benchmark.seed(self.library, experiment, holdout_count=5)
        result = w3_shadow_benchmark.score(self.library, experiment)
        self.assertFalse(result["gates"]["production_eligible"])
        self.assertEqual(result["validation"]["status"], "incomplete")

    def test_teacher_confirmed_valid_supplement_counts_as_correct(self):
        label = {
            "target_count": 2,
            "correct_target_count": 2,
            "target_judgments": [
                {"target_id": "T1", "verdict": "correct"},
                {
                    "target_id": "T2",
                    "verdict": "valid-supplement",
                    "teacher_confirmed": True,
                    "validation": {
                        "prompt_constraints": "题干没有排除周期等待",
                        "recomputable_relation": "Δ=nT 时相位复现",
                        "boundary_cases": "n 为正整数且原标准分支保留",
                    },
                },
            ],
        }
        correct, supplements, judgments, errors = (
            w3_shadow_benchmark.resolved_target_judgments(label, entry_id="entry")
        )
        self.assertEqual(errors, [])
        self.assertEqual(correct, 2)
        self.assertEqual(supplements, 1)
        self.assertEqual(len(judgments), 2)

    def test_unconfirmed_supplement_is_not_silently_scored(self):
        label = {
            "target_count": 1,
            "correct_target_count": 1,
            "target_judgments": [
                {"target_id": "T1", "verdict": "valid-supplement"}
            ],
        }
        _, _, _, errors = w3_shadow_benchmark.resolved_target_judgments(
            label, entry_id="entry"
        )
        self.assertTrue(any("three-part deterministic validation" in item for item in errors))

    def test_w2_target_judgments_require_exact_frozen_coverage(self):
        correct, judgments, errors = (
            w3_shadow_benchmark.resolved_w2_target_judgments(
                {
                    "w2_correct_target_count": 1,
                    "w2_target_judgments": [
                        {"target_id": "T1", "verdict": "correct"},
                        {"target_id": "T2", "verdict": "incorrect"},
                    ],
                },
                expected_target_ids={"T1", "T2"},
                entry_id="entry",
            )
        )
        self.assertEqual(correct, 1)
        self.assertEqual(len(judgments), 2)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
