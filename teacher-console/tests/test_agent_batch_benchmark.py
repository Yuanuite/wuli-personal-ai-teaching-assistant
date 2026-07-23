import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = ROOT / "teacher-console" / "scripts" / "agent_batch_benchmark.py"
SPEC = importlib.util.spec_from_file_location("agent_batch_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)

import kb  # noqa: E402
import knowledge_store  # noqa: E402


class AgentBatchBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.jobs = Path(self.temp.name) / "jobs"
        self.jobs.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_job(self, name: str, payload: dict):
        value = {
            "schema_version": 1,
            "id": name,
            "entry_id": name,
            "kind": "source.clean",
            "status": "completed",
            "created_at": "2026-07-22T10:00:00+08:00",
            "started_at": "2026-07-22T10:00:10+08:00",
            "completed_at": "2026-07-22T10:01:10+08:00",
            "result": {"status": "completed", "provider": "codex", "model": "fast", "usage": {"total_tokens": 100}},
        }
        value.update(payload)
        (self.jobs / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_summarizes_timing_provider_usage_and_parallelism(self):
        self.write_job("a", {})
        self.write_job(
            "b",
            {
                "created_at": "2026-07-22T10:00:00+08:00",
                "started_at": "2026-07-22T10:00:20+08:00",
                "completed_at": "2026-07-22T10:02:20+08:00",
                "result": {"status": "completed", "provider": "claude", "model": "slow", "usage": {"total_tokens": 200}},
            },
        )
        records = benchmark.load_records(self.jobs)
        report = benchmark.summarize(records)
        self.assertEqual(report["total_jobs"], 2)
        self.assertEqual(report["wall_seconds"], 140)
        self.assertEqual(report["max_parallel"], 2)
        source = report["kinds"]["source.clean"]
        self.assertEqual(source["count"], 2)
        self.assertEqual(source["wait"]["avg_seconds"], 15)
        self.assertEqual(source["run"]["p90_seconds"], 114)
        self.assertEqual(source["providers"], {"claude": 1, "codex": 1})
        self.assertEqual(source["usage_total_tokens"], 300)

    def test_classifies_common_failure_types(self):
        self.write_job(
            "failed-validation",
            {
                "status": "failed",
                "completed_at": "2026-07-22T10:00:30+08:00",
                "result": {"status": "failed", "provider": "claude", "validation_errors": ["missing problem"]},
            },
        )
        self.write_job(
            "failed-timeout",
            {
                "status": "failed",
                "completed_at": "2026-07-22T10:00:40+08:00",
                "error": "provider timeout",
                "result": {"status": "failed", "provider": "codex"},
            },
        )
        report = benchmark.summarize(benchmark.load_records(self.jobs))
        failures = report["kinds"]["source.clean"]["failure_types"]
        self.assertEqual(failures["candidate_validation_failed"], 1)
        self.assertEqual(failures["provider_timeout"], 1)

    def test_prefers_structured_record_failure_type_for_worker_failures(self):
        self.write_job(
            "interrupted",
            {
                "status": "failed",
                "completed_at": "2026-07-22T10:00:30+08:00",
                "failure_type": "worker_interrupted",
                "error": "教师工作台重启",
            },
        )
        report = benchmark.summarize(benchmark.load_records(self.jobs))
        self.assertEqual(report["kinds"]["source.clean"]["failure_types"]["worker_interrupted"], 1)

    def test_summarizes_failure_repair_outcomes(self):
        self.write_job(
            "recovered",
            {"result": {
                "status": "completed",
                "provider": "codex",
                "failure_repair": {"status": "recovered", "retry_count": 1},
            }},
        )
        report = benchmark.summarize(benchmark.load_records(self.jobs))
        self.assertEqual(report["kinds"]["source.clean"]["repair_outcomes"], {"recovered": 1})

    def test_filters_kind_and_batch(self):
        self.write_job("source", {"batch_id": "batch-1"})
        self.write_job("answer", {"kind": "answer.revise", "batch_id": "batch-2"})

        class Args:
            kind = "source.clean"
            batch_id = "batch-1"
            since = None
            until = None

        filtered = [record for record in benchmark.load_records(self.jobs) if benchmark.record_passes_filters(record, Args)]
        self.assertEqual([item["id"] for item in filtered], ["source"])

    def test_record_benchmark_appends_library_event_and_rebuilds_store(self):
        library = Path(self.temp.name) / "library"
        kb.init_library(library)
        self.write_job("a", {})
        report = benchmark.summarize(benchmark.load_records(self.jobs))
        event = benchmark.record_benchmark(library, report, {"kind": "source.clean", "jobs_dir": str(self.jobs)})
        self.assertEqual(event["entry_id"], "__library__")
        self.assertEqual(event["task_type"], "scheduler.benchmark")
        self.assertEqual(event["result"]["kinds"]["source.clean"]["usage_total_count"], 100)
        self.assertNotIn("usage_total_tokens", event["result"]["kinds"]["source.clean"])
        self.assertTrue((library / "indexes" / "candidate-archive.jsonl").is_file())
        rebuilt = knowledge_store.rebuild(library)
        self.assertEqual(rebuilt["scheduler_benchmarks"], 1)
        evidence = knowledge_store.query(library, "source clean scheduler", mode="audit")
        self.assertEqual(evidence["scheduler_benchmarks"][0]["event_id"], event["event_id"])
        self.assertEqual(evidence["scheduler_benchmarks"][0]["report"]["total_jobs"], 1)


if __name__ == "__main__":
    unittest.main()
