#!/usr/bin/env python3
"""Summarize teacher-console Agent job timing, concurrency, and failures.

This script is intentionally read-only.  It inspects persisted job records under
``student-error-library/.cache/agent-jobs`` and turns them into a benchmark
report for Scheduler tuning, provider comparison, and later Evolve feedback.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"completed", "failed"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds())


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def rounded(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def summarize_values(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "avg_seconds": None, "p50_seconds": None, "p90_seconds": None, "max_seconds": None}
    return {
        "count": len(values),
        "avg_seconds": rounded(sum(values) / len(values)),
        "p50_seconds": rounded(percentile(values, 0.5)),
        "p90_seconds": rounded(percentile(values, 0.9)),
        "max_seconds": rounded(max(values)),
    }


def load_records(jobs_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not jobs_dir.exists():
        return records
    for path in sorted(jobs_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict):
            record["_path"] = str(path)
            records.append(record)
    return records


def result_value(record: dict[str, Any], key: str, default: Any = "") -> Any:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    return result.get(key, record.get(key, default))


def usage_total(record: dict[str, Any]) -> int:
    usage = result_value(record, "usage", {})
    if not isinstance(usage, dict):
        return 0
    try:
        return int(usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def failure_type(record: dict[str, Any]) -> str:
    result = record.get("result") if isinstance(record.get("result"), dict) else {}
    if isinstance(result.get("failure_type"), str) and result["failure_type"]:
        return result["failure_type"]
    if isinstance(record.get("failure_type"), str) and record["failure_type"]:
        return record["failure_type"]
    if result.get("validation_errors"):
        return "candidate_validation_failed"
    if result.get("unauthorized_changes"):
        return "unauthorized_change"
    message = " ".join(str(value) for value in (record.get("error"), result.get("message"), result.get("stderr")) if value).lower()
    if "timeout" in message or "timed out" in message or "超时" in message:
        return "provider_timeout"
    if "rate limit" in message or "429" in message or "限流" in message:
        return "provider_rate_limited"
    if "truncated" in message or "截断" in message:
        return "output_truncated"
    if "canonical" in message or "发生变化" in message:
        return "canonical_changed"
    return "unknown_failed" if record.get("status") == "failed" else ""


def record_passes_filters(record: dict[str, Any], args: argparse.Namespace) -> bool:
    if args.kind and record.get("kind") != args.kind:
        return False
    if args.batch_id and record.get("batch_id") != args.batch_id:
        return False
    created = parse_time(record.get("created_at"))
    if args.since and created and created < parse_time(args.since):
        return False
    if args.until and created and created > parse_time(args.until):
        return False
    return True


def max_parallel(records: list[dict[str, Any]], *, kind: str | None = None) -> int:
    events: list[tuple[datetime, int]] = []
    for record in records:
        if kind and record.get("kind") != kind:
            continue
        start = parse_time(record.get("started_at"))
        end = parse_time(record.get("completed_at"))
        if not start or not end:
            continue
        events.append((start, 1))
        events.append((end, -1))
    running = peak = 0
    for _time, delta in sorted(events, key=lambda item: (item[0], item[1])):
        running += delta
        peak = max(peak, running)
    return peak


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_kind[str(record.get("kind", "unknown"))].append(record)

    created_times = [parse_time(item.get("created_at")) for item in records]
    completed_times = [parse_time(item.get("completed_at")) for item in records]
    created_times = [item for item in created_times if item]
    completed_times = [item for item in completed_times if item]
    wall_seconds = seconds_between(min(created_times) if created_times else None, max(completed_times) if completed_times else None)

    summary: dict[str, Any] = {
        "schema_version": 1,
        "total_jobs": len(records),
        "completed": sum(1 for item in records if item.get("status") == "completed"),
        "failed": sum(1 for item in records if item.get("status") == "failed"),
        "running_or_queued": sum(1 for item in records if item.get("status") not in TERMINAL_STATUSES),
        "wall_seconds": rounded(wall_seconds),
        "max_parallel": max_parallel(records),
        "kinds": {},
    }

    for kind, items in sorted(by_kind.items()):
        wait_values = []
        run_values = []
        total_values = []
        providers: Counter[str] = Counter()
        models: Counter[str] = Counter()
        failures: Counter[str] = Counter()
        repair_outcomes: Counter[str] = Counter()
        tokens = 0
        for item in items:
            created = parse_time(item.get("created_at"))
            started = parse_time(item.get("started_at"))
            completed = parse_time(item.get("completed_at"))
            wait = seconds_between(created, started)
            runtime = seconds_between(started, completed)
            total = seconds_between(created, completed)
            if wait is not None:
                wait_values.append(wait)
            if runtime is not None:
                run_values.append(runtime)
            if total is not None:
                total_values.append(total)
            provider = str(result_value(item, "provider", "") or "unknown")
            model = str(result_value(item, "model", result_value(item, "model_id", "")) or "unknown")
            providers[provider] += 1
            models[model] += 1
            tokens += usage_total(item)
            failure = failure_type(item)
            if failure:
                failures[failure] += 1
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            repair = result.get("failure_repair") if isinstance(result.get("failure_repair"), dict) else {}
            if repair.get("status"):
                repair_outcomes[str(repair["status"])] += 1
        summary["kinds"][kind] = {
            "count": len(items),
            "completed": sum(1 for item in items if item.get("status") == "completed"),
            "failed": sum(1 for item in items if item.get("status") == "failed"),
            "running_or_queued": sum(1 for item in items if item.get("status") not in TERMINAL_STATUSES),
            "max_parallel": max_parallel(items, kind=kind),
            "wait": summarize_values(wait_values),
            "run": summarize_values(run_values),
            "total": summarize_values(total_values),
            "providers": dict(sorted(providers.items())),
            "models": dict(sorted(models.items())),
            "failure_types": dict(sorted(failures.items())),
            "repair_outcomes": dict(sorted(repair_outcomes.items())),
            "usage_total_tokens": tokens,
        }
    return summary


def record_benchmark(library: Path, report: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    import candidate_archive

    archive_report = json.loads(json.dumps(report, ensure_ascii=False))
    for item in archive_report.get("kinds", {}).values():
        if isinstance(item, dict) and "usage_total_tokens" in item:
            item["usage_total_count"] = item.pop("usage_total_tokens")
    event = candidate_archive.append_library_event(
        library,
        task_type="scheduler.benchmark",
        actor="system",
        event_type="performance-benchmark",
        status="completed",
        summary=(
            f"Agent benchmark: {report.get('total_jobs', 0)} jobs, "
            f"{report.get('completed', 0)} completed, {report.get('failed', 0)} failed, "
            f"wall={report.get('wall_seconds')}s, max_parallel={report.get('max_parallel')}"
        ),
        request=request,
        result=archive_report,
    )
    try:
        import knowledge_store

        event["knowledge_store"] = knowledge_store.rebuild(library)
    except Exception as exc:  # noqa: BLE001
        event["knowledge_store"] = {"status": "skipped", "error": str(exc)}
    return event


def print_markdown(report: dict[str, Any]) -> None:
    print("# Agent Batch Benchmark")
    print()
    print(
        f"- total: {report['total_jobs']} jobs; completed: {report['completed']}; "
        f"failed: {report['failed']}; running/queued: {report['running_or_queued']}"
    )
    print(f"- wall_seconds: {report['wall_seconds']}; max_parallel: {report['max_parallel']}")
    print()
    print("| kind | count | ok/fail | max parallel | wait avg/p90 | run avg/p90 | providers | failures |")
    print("|---|---:|---:|---:|---:|---:|---|---|")
    for kind, item in report["kinds"].items():
        providers = ", ".join(f"{key}:{value}" for key, value in item["providers"].items())
        failures = ", ".join(f"{key}:{value}" for key, value in item["failure_types"].items())
        print(
            f"| {kind} | {item['count']} | {item['completed']}/{item['failed']} | {item['max_parallel']} | "
            f"{item['wait']['avg_seconds']}/{item['wait']['p90_seconds']} | "
            f"{item['run']['avg_seconds']}/{item['run']['p90_seconds']} | "
            f"{providers or '-'} | {failures or '-'} |"
        )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    value.add_argument("--jobs-dir", type=Path)
    value.add_argument("--kind")
    value.add_argument("--batch-id")
    value.add_argument("--since", help="ISO timestamp lower bound for created_at")
    value.add_argument("--until", help="ISO timestamp upper bound for created_at")
    value.add_argument("--format", choices=("json", "markdown"), default="json")
    value.add_argument("--record", action="store_true", help="Append this benchmark summary to the library Candidate Archive and rebuild Knowledge Store")
    return value


def main() -> int:
    args = parser().parse_args()
    jobs_dir = args.jobs_dir.expanduser().resolve() if args.jobs_dir else (args.library.expanduser().resolve() / ".cache" / "agent-jobs")
    records = [record for record in load_records(jobs_dir) if record_passes_filters(record, args)]
    report = summarize(records)
    report["jobs_dir"] = str(jobs_dir)
    if args.record:
        request = {
            "kind": args.kind,
            "batch_id": args.batch_id,
            "since": args.since,
            "until": args.until,
            "jobs_dir": str(jobs_dir),
        }
        report["archive_event"] = record_benchmark(args.library.expanduser().resolve(), report, request)
    if args.format == "markdown":
        print_markdown(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
