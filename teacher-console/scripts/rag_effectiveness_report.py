#!/usr/bin/env python3
"""Compare Agent outcomes across Knowledge Store evidence cohorts.

The report is observational: it never disables retrieval or calls a model. It
combines persisted Agent jobs (runtime/cost) with Candidate Archive events
(Evaluator and teacher-review outcomes). Use a fixed eval set for causal A/B.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import candidate_archive  # noqa: E402


TASKS = {"answer.revise", "visualization.model"}
APPROVAL_TASK = {"answer.revise": "answer.approve", "visualization.model": "visualization.approve"}
REWORK_TASKS = {
    "answer.revise": {"answer.save", "answer.revision-request"},
    "visualization.model": {"visualization.model"},
}


def teaching_batch(event: dict[str, Any]) -> str:
    request = event.get("request") if isinstance(event.get("request"), dict) else {}
    if request.get("batch_id"):
        return str(request["batch_id"])
    entry_id = str(event.get("entry_id", ""))
    prefix = entry_id[:8]
    return prefix if len(prefix) == 8 and prefix.isdigit() else "unknown"


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


def cohort(value: dict[str, Any]) -> str:
    context = value.get("evidence_context")
    if not isinstance(context, dict):
        result = value.get("result")
        context = result.get("evidence_context") if isinstance(result, dict) else None
    if not isinstance(context, dict):
        return "legacy-no-rag"
    status = str(context.get("status", "unavailable"))
    try:
        count = int(context.get("reference_count", 0))
    except (TypeError, ValueError):
        count = 0
    if status == "ready" and count > 0:
        return "retrieved"
    if status == "ready":
        return "empty"
    return "unavailable"


def load_jobs(directory: Path) -> list[dict[str, Any]]:
    records = []
    if not directory.exists():
        return records
    for path in sorted(directory.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(item, dict) and item.get("kind") in TASKS:
            records.append(item)
    return records


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _seconds(start: Any, end: Any) -> float | None:
    left, right = parse_time(start), parse_time(end)
    return max(0.0, (right - left).total_seconds()) if left and right else None


def operational_summary(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        grouped[(str(job.get("kind")), cohort(job))].append(job)
    result = {}
    for (task_type, group), items in sorted(grouped.items()):
        runtimes = [_seconds(item.get("started_at"), item.get("completed_at")) for item in items]
        runtimes = [value for value in runtimes if value is not None]
        usage_values = []
        for item in items:
            payload = item.get("result") if isinstance(item.get("result"), dict) else {}
            usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
            value = usage.get("total_tokens")
            if isinstance(value, int) and not isinstance(value, bool):
                usage_values.append(float(value))
        result[f"{task_type}:{group}"] = {
            "task_type": task_type,
            "cohort": group,
            "count": len(items),
            "completed": sum(1 for item in items if item.get("status") == "completed"),
            "failed": sum(1 for item in items if item.get("status") == "failed"),
            "completion_rate": round(sum(1 for item in items if item.get("status") == "completed") / len(items), 4),
            "avg_runtime_seconds": _average(runtimes),
            "avg_usage_total_count": _average(usage_values),
        }
    return result


def teaching_trials(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_entry: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("entry_id") != candidate_archive.LIBRARY_ENTRY_ID:
            by_entry[str(event.get("entry_id", ""))].append(event)
    trials = []
    for entry_events in by_entry.values():
        # Candidate Archive is append-only and its JSONL order is authoritative.
        # created_at currently has second precision, so sorting same-second
        # events by hash-based event_id can invert agent-result and approval.
        ordered = list(entry_events)
        for index, event in enumerate(ordered):
            task_type = str(event.get("task_type", ""))
            if task_type not in TASKS or event.get("event_type") != "agent-result":
                continue
            window = []
            superseded = False
            for following in ordered[index + 1:]:
                if following.get("task_type") == task_type and following.get("event_type") == "agent-result":
                    superseded = True
                    break
                window.append(following)
            approval = next((item for item in window if item.get("task_type") == APPROVAL_TASK[task_type] and item.get("raw_status") == "approved"), None)
            rework = sum(1 for item in window if item.get("task_type") in REWORK_TASKS[task_type])
            evaluation = event.get("evaluation") if isinstance(event.get("evaluation"), dict) else {}
            scores = evaluation.get("scores") if isinstance(evaluation.get("scores"), dict) else {}
            candidate_completed = event.get("raw_status") == "completed"
            trials.append({
                "entry_id": str(event.get("entry_id", "")),
                "teaching_batch": teaching_batch(event),
                "task_type": task_type,
                "cohort": cohort(event),
                "candidate_completed": candidate_completed,
                "approved": approval is not None,
                "pending": candidate_completed and approval is None and not superseded,
                "superseded": candidate_completed and approval is None and superseded,
                "rework_events": rework,
                "seconds_to_approval": _seconds(event.get("created_at"), approval.get("created_at")) if approval else None,
                "scores": {str(key): float(value) for key, value in scores.items() if isinstance(value, (int, float)) and not isinstance(value, bool)},
            })
    return trials


def teaching_summary(trials: list[dict[str, Any]], min_samples: int) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        grouped[(trial["task_type"], trial["cohort"])].append(trial)
    result = {}
    for (task_type, group), items in sorted(grouped.items()):
        approval_times = [item["seconds_to_approval"] for item in items if item["seconds_to_approval"] is not None]
        dimensions = sorted({key for item in items for key in item["scores"]})
        result[f"{task_type}:{group}"] = {
            "task_type": task_type,
            "cohort": group,
            "count": len(items),
            "teaching_batch_count": len({item["teaching_batch"] for item in items if item["teaching_batch"] != "unknown"}),
            "sample_ready": len(items) >= min_samples,
            "candidate_completion_rate": round(sum(item["candidate_completed"] for item in items) / len(items), 4),
            "candidate_failed": sum(not item["candidate_completed"] for item in items),
            "teacher_closed": sum(1 for item in items if item["approved"] or item["rework_events"] > 0),
            "teacher_approval_rate": round(sum(item["approved"] for item in items) / len(items), 4),
            "pending": sum(item["pending"] for item in items),
            "superseded": sum(item["superseded"] for item in items),
            "rework_events": sum(item["rework_events"] for item in items),
            "avg_seconds_to_approval": _average(approval_times),
            "avg_evaluator_scores": {
                dimension: _average([item["scores"][dimension] for item in items if dimension in item["scores"]])
                for dimension in dimensions
            },
        }
    return result


def build_report(library: Path, jobs_dir: Path, *, min_samples: int = 10) -> dict[str, Any]:
    events = candidate_archive.read_library_events(library)
    trials = teaching_trials(events)
    operations = operational_summary(load_jobs(jobs_dir))
    teaching = teaching_summary(trials, min_samples)
    cohorts = sorted({item["cohort"] for item in operations.values()} | {item["cohort"] for item in teaching.values()})
    comparable_tasks = sorted(
        task_type
        for task_type in TASKS
        if teaching.get(f"{task_type}:retrieved", {}).get("sample_ready")
        and teaching.get(f"{task_type}:legacy-no-rag", {}).get("sample_ready")
    )
    return {
        "schema_version": 1,
        "report_type": "rag-effectiveness-observational",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "min_samples_per_group": min_samples,
        "cohorts": cohorts,
        "comparison_ready": bool(comparable_tasks),
        "comparable_tasks": comparable_tasks,
        "operational": operations,
        "teaching_outcomes": teaching,
        "notes": [
            "This is an observational cohort report, not a causal randomized A/B test.",
            "Use a fixed reviewed test set before changing retrieval or routing policy.",
            "Pending trials must not be treated as failed teacher reviews.",
        ],
    }


def record_report(library: Path, report: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    event = candidate_archive.append_library_event(
        library,
        task_type="evolve.observation.rag",
        actor="system",
        event_type="effectiveness-report",
        status="completed",
        summary=(
            f"RAG observation: cohorts={','.join(report.get('cohorts', [])) or 'none'}, "
            f"comparison_ready={report.get('comparison_ready', False)}"
        ),
        request=request,
        result=report,
    )
    try:
        import knowledge_store
        event["knowledge_store"] = knowledge_store.rebuild(library)
    except Exception as exc:  # noqa: BLE001
        event["knowledge_store"] = {"status": "skipped", "error": str(exc)}
    return event


def print_markdown(report: dict[str, Any]) -> None:
    print("# RAG Effectiveness Observation")
    print()
    print(f"- cohorts: {', '.join(report['cohorts']) or '-'}")
    print(f"- comparison_ready: {report['comparison_ready']}; min_samples_per_group: {report['min_samples_per_group']}")
    print("- observational only; use a fixed reviewed test set for causal A/B")
    print()
    print("| task/cohort | n | completion | approval | rework | eval scores |")
    print("|---|---:|---:|---:|---:|---|")
    keys = sorted(set(report["operational"]) | set(report["teaching_outcomes"]))
    for key in keys:
        operation = report["operational"].get(key, {})
        teaching = report["teaching_outcomes"].get(key, {})
        scores = ", ".join(f"{name}={value}" for name, value in teaching.get("avg_evaluator_scores", {}).items())
        print(
            f"| {key} | {max(operation.get('count', 0), teaching.get('count', 0))} | "
            f"{operation.get('completion_rate', teaching.get('candidate_completion_rate', '-'))} | "
            f"{teaching.get('teacher_approval_rate', '-')} | {teaching.get('rework_events', '-')} | {scores or '-'} |"
        )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    value.add_argument("--jobs-dir", type=Path)
    value.add_argument("--min-samples", type=int, default=10)
    value.add_argument("--format", choices=("json", "markdown"), default="json")
    value.add_argument("--record", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    library = args.library.expanduser().resolve()
    jobs_dir = args.jobs_dir.expanduser().resolve() if args.jobs_dir else library / ".cache" / "agent-jobs"
    report = build_report(library, jobs_dir, min_samples=max(1, args.min_samples))
    if args.record:
        report["archive_event"] = record_report(library, report, {"min_samples": args.min_samples, "jobs_dir": str(jobs_dir)})
    if args.format == "markdown":
        print_markdown(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
