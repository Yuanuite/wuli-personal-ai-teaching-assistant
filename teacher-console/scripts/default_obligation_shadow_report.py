#!/usr/bin/env python3
"""Summarize default-obligation shadow suggestions from existing W3 reports.

This script is deliberately read-only with respect to entries and W3 artifacts:
it can recompute diagnostic suggestions from stored blueprints, but it never
mutates ``verification_obligations`` or re-runs solvers.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
sys.path.insert(0, str(CONSOLE))

import problem_decomposition  # noqa: E402

DEFAULT_LIBRARY = PROJECT_ROOT / "student-error-library"
DEFAULT_OUTPUT_JSON = PROJECT_ROOT / "docs" / "reports" / "default-obligation-shadow-report.json"
DEFAULT_OUTPUT_MD = PROJECT_ROOT / "docs" / "reports" / "default-obligation-shadow-report.md"
IPHO_SUMMARY = DEFAULT_LIBRARY / "evals" / "ipho-2021-full-exam-v1" / "results" / "summary.json"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def entry_problem_text(entry: Path) -> str:
    problem_path = entry / "problem.md"
    if problem_path.is_file():
        return problem_path.read_text(encoding="utf-8", errors="replace")
    record = load_json(entry / "record.json")
    review = record.get("source_review", {})
    if isinstance(review, dict):
        text = review.get("problem_text") or review.get("clean_text")
        if isinstance(text, str):
            return text
    return ""


def entry_title(entry: Path, *, fallback: str) -> str:
    record = load_json(entry / "record.json")
    title = record.get("title")
    return str(title).strip() if isinstance(title, str) and title.strip() else fallback


def report_body(raw: dict[str, Any]) -> dict[str, Any]:
    report = raw.get("report")
    return report if isinstance(report, dict) else raw


def report_paths(library: Path) -> list[Path]:
    paths = list((library / "entries").glob("*/w3-shadow-report.json"))
    paths.extend((library / "evals").glob("*/artifacts/*/*w3-shadow-report.json"))
    return sorted(path for path in paths if path.is_file())


def suggestions_for_report(
    report_path: Path,
    *,
    recompute: bool,
    library: Path,
) -> dict[str, Any]:
    raw = load_json(report_path)
    body = report_body(raw)
    entry_id = str(raw.get("entry_id") or report_path.parent.name)
    entry = library / "entries" / entry_id
    if not entry.is_dir() and "artifacts" not in report_path.parts:
        entry = report_path.parent
    title = entry_title(entry, fallback=entry_id) if entry.is_dir() else entry_id
    status = str(raw.get("status") or body.get("status") or "unknown")
    blueprint = body.get("blueprint")
    stored = body.get("default_obligation_suggestions")
    if not isinstance(stored, list):
        stored = []
    source = "stored"
    suggestions = [item for item in stored if isinstance(item, dict)]
    if recompute and isinstance(blueprint, dict):
        problem = entry_problem_text(entry) if entry.is_dir() else ""
        suggestions = problem_decomposition.infer_default_obligation_suggestions(problem, blueprint)
        source = "recomputed-from-blueprint"
    return {
        "entry_id": entry_id,
        "title": title,
        "report_path": display_path(report_path),
        "status": status,
        "suggestion_source": source,
        "blueprint_available": isinstance(blueprint, dict),
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
    }


def ipho_coverage() -> dict[str, Any]:
    summary = load_json(IPHO_SUMMARY)
    if not summary:
        return {
            "status": "missing",
            "path": display_path(IPHO_SUMMARY),
            "reason": "IPhO closed-book summary not found",
        }
    questions = summary.get("questions", [])
    subpart_count = sum(len(item.get("subparts", [])) for item in questions if isinstance(item, dict))
    awarded = sum(float(item.get("awarded_points", 0.0)) for item in questions if isinstance(item, dict))
    maximum = sum(float(item.get("maximum_points", 0.0)) for item in questions if isinstance(item, dict))
    return {
        "status": "not-covered-by-w3-blueprint-recompute",
        "path": display_path(IPHO_SUMMARY),
        "experiment_id": summary.get("experiment_id"),
        "question_count": len([item for item in questions if isinstance(item, dict)]),
        "subpart_count": subpart_count,
        "score": {"awarded_points": awarded, "maximum_points": maximum},
        "reason": (
            "The IPhO full-exam harness answers official subparts directly through "
            "Agent Gateway and does not persist W3 decomposition blueprints; default "
            "obligation shadow suggestions therefore cannot be audited from this "
            "summary without a separate blueprint pass."
        ),
    }


def summarize(
    library: Path = DEFAULT_LIBRARY,
    *,
    recompute: bool = True,
    include_ipho: bool = True,
) -> dict[str, Any]:
    cases = [suggestions_for_report(path, recompute=recompute, library=library) for path in report_paths(library)]
    triggered = [case for case in cases if int(case["suggestion_count"]) > 0]
    rules = Counter(
        str(item.get("rule_id", "unknown"))
        for case in triggered
        for item in case.get("suggestions", [])
        if isinstance(item, dict)
    )
    targets = Counter(
        str(item.get("target_id", "unknown"))
        for case in triggered
        for item in case.get("suggestions", [])
        if isinstance(item, dict)
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": problem_decomposition.DEFAULT_OBLIGATION_POLICY,
        "mode": "shadow-only",
        "recompute_from_blueprint": recompute,
        "library": str(library.relative_to(PROJECT_ROOT) if library.is_relative_to(PROJECT_ROOT) else library),
        "totals": {
            "w3_report_count": len(cases),
            "blueprint_available_count": sum(1 for case in cases if case["blueprint_available"]),
            "triggered_case_count": len(triggered),
            "suggestion_count": sum(int(case["suggestion_count"]) for case in cases),
            "rule_counts": dict(sorted(rules.items())),
            "target_counts": dict(sorted(targets.items())),
        },
        "safety": {
            "solver_affected": False,
            "verification_obligations_mutated": False,
            "verified_gate_affected": False,
            "manual_review_required": True,
            "promotion_recommendation": "do-not-promote-to-hard-gate-yet",
        },
        "cases": cases,
    }
    if include_ipho:
        report["ipho_full_exam"] = ipho_coverage()
    return report


def markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# 默认义务 Shadow 触发报告",
        "",
        f"- 生成时间：`{report['generated_at']}`",
        f"- 策略：`{report['policy']}`",
        f"- 模式：`{report['mode']}`，只统计，不影响 Solver / VERIFIED / 交付",
        f"- W3 报告数：{totals['w3_report_count']}",
        f"- 可重算 blueprint 数：{totals['blueprint_available_count']}",
        f"- 触发题目数：{totals['triggered_case_count']}",
        f"- 建议总数：{totals['suggestion_count']}",
        "",
        "## 结论",
        "",
    ]
    if totals["suggestion_count"] == 0:
        lines.extend([
            "当前存量 W3 报告中没有触发默认义务建议。",
            "",
            "这说明目前这条规则足够保守，但也意味着它还没有证明能稳定补充盲点；不应升级为硬门禁。",
        ])
    else:
        lines.extend([
            "当前规则已经能从旧 W3 blueprint 中识别出潜在“默认枚举全部物理解支”义务。",
            "",
            "这些命中仍需要教师/开发者逐条审查；在误伤率被确认前，不建议升级为硬 Gate。",
        ])
    lines.extend([
        "",
        "## 安全边界",
        "",
        "- 不重新调用 Solver；",
        "- 不修改 `verification_obligations`；",
        "- 不影响 `VERIFIED`、评分或交付；",
        "- 所有命中均标记为人工审查材料。",
        "",
    ])
    ipho = report.get("ipho_full_exam")
    if isinstance(ipho, dict):
        lines.extend([
            "## IPhO 整卷覆盖状态",
            "",
            f"- 状态：`{ipho.get('status')}`",
            f"- 路径：`{ipho.get('path')}`",
        ])
        if ipho.get("score"):
            score = ipho["score"]
            lines.append(f"- 已有整卷成绩：{score.get('awarded_points')}/{score.get('maximum_points')}")
        lines.extend(["", str(ipho.get("reason", "")).strip(), ""])
    lines.extend(["## 触发明细", ""])
    triggered = [case for case in report["cases"] if case["suggestion_count"]]
    if not triggered:
        lines.append("无。")
    for case in triggered:
        lines.extend([
            f"### {case['title']}",
            "",
            f"- Entry：`{case['entry_id']}`",
            f"- 报告：`{case['report_path']}`",
            f"- 建议数：{case['suggestion_count']}",
            "",
        ])
        for item in case["suggestions"]:
            lines.extend([
                f"- `{item.get('rule_id', 'unknown')}` → target `{item.get('target_id', 'unknown')}`",
                f"  - 触发文本：{item.get('trigger_text', '')}",
                f"  - 检查建议：{item.get('check', '')}",
                f"  - 风险：{item.get('risk', 'unknown')}；状态：{item.get('status', 'suggested')}",
            ])
        lines.append("")
    lines.extend([
        "## 建议",
        "",
        "继续保持 shadow-only。下一步只需要对触发明细做人工标注：`useful` / `false-positive` / `already-covered`，攒够样本后再决定是否进入软提示层。",  # noqa: E501
        "",
    ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_OUTPUT_JSON)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--no-recompute", action="store_true")
    parser.add_argument("--skip-ipho", action="store_true")
    args = parser.parse_args(argv)

    report = summarize(
        args.library,
        recompute=not args.no_recompute,
        include_ipho=not args.skip_ipho,
    )
    write_json(args.output_json, report)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(markdown(report), encoding="utf-8")
    print(f"wrote {args.output_md}")
    print(f"wrote {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
