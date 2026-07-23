#!/usr/bin/env python3
"""Generate evidence-gated, read-only recommendations for Wuli's slow loop.

This script aggregates retrieval, RAG teaching outcomes, and Agent scheduling
evidence.  It never edits routing, concurrency, prompts, retrieval parameters,
canonical entries, approvals, or publication state.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import candidate_archive  # noqa: E402
import knowledge_store  # noqa: E402


def _load_script(name: str):
    path = Path(__file__).resolve().with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rag_reporter = _load_script("rag_effectiveness_report")
retrieval_reporter = _load_script("retrieval_benchmark")
scheduler_reporter = _load_script("agent_batch_benchmark")


def _rate(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _previous_slow_reports(library: Path) -> list[dict[str, Any]]:
    reports = []
    for event in candidate_archive.read_library_events(library):
        if event.get("task_type") == "evolve.observation.slow-loop" and isinstance(event.get("result"), dict):
            reports.append(event["result"])
    return reports


def _latest_slow_event(library: Path) -> dict[str, Any] | None:
    matches = [
        event for event in candidate_archive.read_library_events(library)
        if event.get("task_type") == "evolve.observation.slow-loop" and isinstance(event.get("result"), dict)
    ]
    return matches[-1] if matches else None


def _teacher_strategy_confirmed(library: Path) -> bool:
    latest = _latest_slow_event(library)
    if not latest:
        return False
    return any(
        event.get("task_type") == "evolve.strategy.confirm"
        and event.get("actor") == "teacher"
        and event.get("raw_status") == "approved"
        and isinstance(event.get("request"), dict)
        and event["request"].get("slow_loop_event_id") == latest.get("event_id")
        for event in candidate_archive.read_library_events(library)
    )


def analyze(
    rag: dict[str, Any],
    retrieval: dict[str, Any],
    scheduler: dict[str, Any],
    *,
    previous_reports: list[dict[str, Any]] | None = None,
    teacher_strategy_confirmed: bool = False,
) -> dict[str, Any]:
    previous_reports = previous_reports or []
    operations = rag.get("operational", {}) if isinstance(rag.get("operational"), dict) else {}
    teaching = rag.get("teaching_outcomes", {}) if isinstance(rag.get("teaching_outcomes"), dict) else {}
    retrieved_completed = sum(
        int(item.get("completed", 0))
        for item in operations.values()
        if item.get("cohort") == "retrieved"
    )
    teacher_closed = sum(
        int(item.get("teacher_closed", 0))
        for item in teaching.values()
        if item.get("cohort") == "retrieved"
    )
    weekly_ready = retrieved_completed >= 20 and teacher_closed >= 10
    scheduler_kinds = scheduler.get("kinds", {}) if isinstance(scheduler.get("kinds"), dict) else {}
    terminal_attempts = sum(
        int(item.get("completed", 0)) + int(item.get("failed", 0))
        for item in scheduler_kinds.values()
        if isinstance(item, dict)
    )
    structured_failures = sum(
        int(count)
        for item in scheduler_kinds.values()
        if isinstance(item, dict)
        for code, count in (item.get("failure_types", {}) or {}).items()
        if code != "unknown_failed"
    )
    reliability_ready = terminal_attempts >= 5 and structured_failures >= 1

    comparable = []
    for task_type in sorted(rag_reporter.TASKS):
        retrieved = teaching.get(f"{task_type}:retrieved", {})
        legacy = teaching.get(f"{task_type}:legacy-no-rag", {})
        if (
            int(retrieved.get("count", 0)) >= 10
            and int(legacy.get("count", 0)) >= 10
            and int(retrieved.get("teaching_batch_count", 0)) >= 2
            and int(legacy.get("teaching_batch_count", 0)) >= 2
        ):
            comparable.append(task_type)
    strategy_ready = bool(comparable)

    recommendations: list[dict[str, Any]] = []
    if retrieval.get("fixed_set_ready") and retrieval.get("upgrade_recommended"):
        recommendations.append({
            "code": "retrieval.tag-weighting-review",
            "area": "retrieval",
            "risk": "low",
            "proposal": "在固定集上试验 JSON 标签过滤与字段加权，保持 query()/build_agent_evidence() 接口不变。",
            "evidence": {
                "recall_at_5": retrieval.get("overall", {}).get("recall", {}).get("@5"),
                "teacher_phrase_hit_at_5": retrieval.get("by_category", {}).get("teacher_phrase", {}).get("hit_rate", {}).get("@5"),
            },
            "apply": False,
        })

    if strategy_ready:
        for task_type in comparable:
            retrieved = teaching[f"{task_type}:retrieved"]
            legacy = teaching[f"{task_type}:legacy-no-rag"]
            approval_delta = round(_rate(retrieved.get("teacher_approval_rate")) - _rate(legacy.get("teacher_approval_rate")), 4)
            if approval_delta >= 0.1:
                recommendations.append({
                    "code": f"rag.keep-and-canary.{task_type}",
                    "area": "rag",
                    "risk": "low",
                    "proposal": f"保留 {task_type} 的证据注入，并仅在固定集上比较 top-k/字符预算候选。",
                    "evidence": {"approval_rate_delta": approval_delta},
                    "apply": False,
                })
            elif approval_delta <= -0.1:
                recommendations.append({
                    "code": f"rag.audit-budget.{task_type}",
                    "area": "rag",
                    "risk": "medium",
                    "proposal": f"审查 {task_type} 的证据相关性和预算；先做离线回放，不在线关闭 RAG。",
                    "evidence": {"approval_rate_delta": approval_delta},
                    "apply": False,
                })

    for kind, item in (scheduler.get("kinds", {}) or {}).items():
        run_p90 = item.get("run", {}).get("p90_seconds") if isinstance(item.get("run"), dict) else None
        failures = item.get("failure_types", {}) if isinstance(item.get("failure_types"), dict) else {}
        count = max(1, int(item.get("count", 0)))
        dominant_failure = max(failures, key=failures.get) if failures else ""
        if count >= 5 and isinstance(run_p90, (int, float)) and run_p90 > 60:
            recommendations.append({
                "code": f"scheduler.latency-review.{kind}",
                "area": "scheduler",
                "risk": "low",
                "proposal": f"复盘 {kind} 的 provider 路由、超时和任务拆分；当前 P90 超过 60 秒。",
                "evidence": {"run_p90_seconds": run_p90},
                "apply": False,
            })
        if count >= 5 and dominant_failure and dominant_failure != "unknown_failed" and failures[dominant_failure] / count >= 0.2:
            recommendations.append({
                "code": f"scheduler.failure-review.{kind}.{dominant_failure}",
                "area": "scheduler",
                "risk": "low",
                "proposal": f"针对 {kind} 的高频失败 {dominant_failure} 优先改验证器、提示或 provider 健康检查。",
                "evidence": {"failure_type": dominant_failure, "share": round(failures[dominant_failure] / count, 4)},
                "apply": False,
            })

    codes = sorted(item["code"] for item in recommendations)
    strategy_codes = sorted(item["code"] for item in recommendations if item["area"] in {"rag", "retrieval"})
    previous = previous_reports[-1] if previous_reports else {}
    previous_codes = sorted(previous.get("strategy_recommendation_codes", [])) if isinstance(previous, dict) else []
    same_direction_twice = bool(strategy_codes and strategy_codes == previous_codes)
    current_recall = retrieval.get("overall", {}).get("recall", {}).get("@5")
    previous_recall = previous.get("retrieval_recall_at_5") if isinstance(previous, dict) else None
    fixed_set_non_regression = bool(
        retrieval.get("fixed_set_ready")
        and isinstance(current_recall, (int, float))
        and isinstance(previous_recall, (int, float))
        and current_recall >= previous_recall
    )
    policy_change_ready = bool(
        strategy_ready
        and same_direction_twice
        and fixed_set_non_regression
        and teacher_strategy_confirmed
    )
    auto_apply_executor_ready = False
    auto_apply_ready = bool(policy_change_ready and teacher_closed >= 50 and auto_apply_executor_ready)

    blockers = []
    if not weekly_ready:
        blockers.append(f"只读周报还需 {max(0, 20 - retrieved_completed)} 个已完成 RAG 任务、{max(0, 10 - teacher_closed)} 个教师闭环。")
    if not reliability_ready:
        blockers.append(f"失败可靠性观察还需至少 {max(0, 5 - terminal_attempts)} 个终态 Agent 任务，并至少出现 1 个结构化失败。")
    if not strategy_ready:
        blockers.append("策略建议尚缺同任务双 cohort 各 10 个样本，并需各跨至少两个教学批次。")
    if not retrieval.get("fixed_set_ready"):
        blockers.append("固定检索集尚未达到 30 条教师 approved 且覆盖四类查询。")
    if not same_direction_twice:
        blockers.append("尚无连续两期方向一致的慢循环报告。")
    if not teacher_strategy_confirmed:
        blockers.append("尚无教师对策略候选的显式确认事件。")
    blockers.append("自动应用仍缺版本化策略、回滚点、边界约束和 canary 执行器。")

    return {
        "schema_version": 1,
        "report_type": "slow-loop-read-only",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "readiness": {
            "weekly_report_ready": weekly_ready,
            "reliability_observation_ready": reliability_ready,
            "strategy_recommendation_ready": strategy_ready,
            "policy_change_ready": policy_change_ready,
            "auto_apply_ready": auto_apply_ready,
        },
        "evidence_counts": {
            "retrieved_agent_completed": retrieved_completed,
            "retrieved_teacher_closed": teacher_closed,
            "agent_terminal_attempts": terminal_attempts,
            "agent_structured_failures": structured_failures,
            "comparable_tasks": comparable,
            "retrieval_approved_cases": retrieval.get("validation", {}).get("status_counts", {}).get("approved", 0),
        },
        "recommendation_codes": codes,
        "strategy_recommendation_codes": strategy_codes,
        "recommendations": recommendations,
        "retrieval_recall_at_5": current_recall,
        "blockers": blockers,
        "safety": {
            "mutates_policy": False,
            "calls_model": False,
            "can_approve": False,
            "can_publish": False,
            "auto_apply_executor_ready": auto_apply_executor_ready,
        },
    }


def build_report(library: Path, jobs_dir: Path, cases_path: Path) -> dict[str, Any]:
    rag = rag_reporter.build_report(library, jobs_dir, min_samples=10)
    scheduler = scheduler_reporter.summarize(scheduler_reporter.load_records(jobs_dir))
    cases = retrieval_reporter.load_cases(cases_path) if cases_path.exists() else []
    if cases:
        retrieval = retrieval_reporter.run_benchmark(library, cases, top_k=5, include_draft=False)
    else:
        retrieval = {
            "fixed_set_ready": False,
            "upgrade_recommended": False,
            "validation": {"status_counts": {}, "warnings": ["retrieval case file is missing"]},
            "overall": {"recall": {"@5": None}},
            "by_category": {},
        }
    return analyze(
        rag,
        retrieval,
        scheduler,
        previous_reports=_previous_slow_reports(library),
        teacher_strategy_confirmed=_teacher_strategy_confirmed(library),
    )


def record_report(library: Path, report: dict[str, Any]) -> dict[str, Any]:
    readiness = report.get("readiness", {})
    if not (readiness.get("weekly_report_ready") or readiness.get("reliability_observation_ready")):
        raise ValueError("--record requires either 20 completed RAG tasks + 10 teacher closures, or 5 terminal Agent attempts with a structured failure")
    event = candidate_archive.append_library_event(
        library,
        task_type="evolve.observation.slow-loop",
        actor="system",
        event_type="slow-loop-report",
        status="completed",
        summary=(
            f"Slow loop: recommendations={len(report.get('recommendations', []))}, "
            f"strategy_ready={report.get('readiness', {}).get('strategy_recommendation_ready', False)}, "
            f"policy_ready={report.get('readiness', {}).get('policy_change_ready', False)}"
        ),
        request={"report_type": "weekly"},
        result=report,
    )
    try:
        event["knowledge_store"] = knowledge_store.rebuild(library)
    except Exception as exc:  # noqa: BLE001
        event["knowledge_store"] = {"status": "skipped", "error": str(exc)}
    return event


def confirm_strategy(library: Path, *, reviewer: str, note: str = "") -> dict[str, Any]:
    latest = _latest_slow_event(library)
    if not latest:
        raise ValueError("no recorded slow-loop report is available for teacher confirmation")
    result = latest.get("result", {}) if isinstance(latest.get("result"), dict) else {}
    codes = result.get("strategy_recommendation_codes", [])
    if not isinstance(codes, list) or not codes:
        raise ValueError("latest slow-loop report contains no strategy recommendation to confirm")
    return candidate_archive.append_library_event(
        library,
        task_type="evolve.strategy.confirm",
        actor="teacher",
        event_type="strategy-review",
        status="approved",
        summary=f"Teacher {reviewer} confirmed {len(codes)} strategy recommendation(s). {note}".strip(),
        request={"slow_loop_event_id": latest.get("event_id"), "strategy_recommendation_codes": codes},
        result={"reviewer": reviewer, "note": note, "applies_policy": False},
    )


def print_markdown(report: dict[str, Any]) -> None:
    readiness = report["readiness"]
    counts = report["evidence_counts"]
    print("# Wuli Slow-loop Report")
    print()
    print(f"- weekly_report_ready: {readiness['weekly_report_ready']}")
    print(f"- reliability_observation_ready: {readiness['reliability_observation_ready']}")
    print(f"- strategy_recommendation_ready: {readiness['strategy_recommendation_ready']}")
    print(f"- policy_change_ready: {readiness['policy_change_ready']}")
    print(f"- auto_apply_ready: {readiness['auto_apply_ready']}")
    print(f"- RAG completed / teacher closed: {counts['retrieved_agent_completed']} / {counts['retrieved_teacher_closed']}")
    print(f"- Agent terminal / structured failures: {counts['agent_terminal_attempts']} / {counts['agent_structured_failures']}")
    print(f"- retrieval approved cases: {counts['retrieval_approved_cases']}")
    print()
    if report["recommendations"]:
        print("## Read-only recommendations")
        print()
        for item in report["recommendations"]:
            print(f"- `{item['code']}`: {item['proposal']}")
        print()
    print("## Blockers")
    print()
    for item in report["blockers"]:
        print(f"- {item}")


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    value.add_argument("--jobs-dir", type=Path)
    value.add_argument("--cases", type=Path)
    value.add_argument("--format", choices=("json", "markdown"), default="json")
    value.add_argument("--record", action="store_true")
    value.add_argument("--confirm-strategy", action="store_true")
    value.add_argument("--reviewer", default="teacher")
    value.add_argument("--note", default="")
    return value


def main() -> int:
    args = parser().parse_args()
    library = args.library.expanduser().resolve()
    jobs_dir = args.jobs_dir.expanduser().resolve() if args.jobs_dir else library / ".cache" / "agent-jobs"
    cases_path = args.cases.expanduser().resolve() if args.cases else retrieval_reporter.default_cases_path(library)
    if args.confirm_strategy:
        event = confirm_strategy(library, reviewer=args.reviewer, note=args.note)
        print(json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    report = build_report(library, jobs_dir, cases_path)
    if args.record:
        report["archive_event"] = record_report(library, report)
    if args.format == "markdown":
        print_markdown(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
