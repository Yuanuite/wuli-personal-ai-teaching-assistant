#!/usr/bin/env python3
"""Pipeline cost-quality summary report.

Aggregate per-entry quality scores, token consumption, and timing to produce
a unified cost × quality × latency dashboard.  Helps answer:

  - Does model X produce better answers at lower token cost?
  - What is the average token burn per "quality point"?
  - Which pipeline stages are the latency bottlenecks?

Output is Markdown (or JSON) suitable for review, commit, or Slack.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "teacher-console"))
sys.path.insert(0, str(PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"))

from scripts.pipeline_quality_eval import (  # noqa: E402
    ENTRIES,
    evaluate_entry,
    load_json,
)


def _fmt_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    return f"{seconds / 60:.1f}m"


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def build_summary(entry_ids: list[str] | None = None) -> dict:
    """Evaluate all (or specified) entries and produce a cross‑entry summary."""
    if entry_ids:
        targets = []
        for eid in entry_ids:
            path = ENTRIES / eid if not (ENTRIES / eid).is_dir() else ENTRIES / eid
            # Try prefix match
            if not path.is_dir():
                for child in sorted(ENTRIES.iterdir()):
                    if child.name.startswith(eid):
                        path = child
                        break
            if path.is_dir():
                targets.append(path)
    else:
        targets = sorted(
            (e for e in ENTRIES.iterdir() if load_json(e / "pipeline.json").get("state") == "delivered"),
        )

    if not targets:
        return {"error": "no delivered entries found", "entries": []}

    results = [evaluate_entry(e.name) for e in targets]

    # Aggregate
    total_tokens = 0
    total_pipeline_seconds: float = 0
    timing_samples = 0
    scores: list[int] = []
    token_quality_list: list[float] = []
    stage_timings: dict[str, list[float]] = {}
    stage_tokens: dict[str, list[int]] = {}
    model_counts: dict[str, int] = {}
    pass_count = 0

    for r in results:
        if r.get("error"):
            continue
        scores.append(r["score"])
        if r.get("pass"):
            pass_count += 1

        tele = r["telemetry"]
        t_efficiency = tele.get("token_efficiency", {})
        total_tokens += t_efficiency.get("total_tokens", 0)

        # Quality-per-token efficiency
        if t_efficiency.get("total_tokens", 0) > 0:
            token_quality_list.append(t_efficiency.get("tokens_per_quality_point", 0))

        # Stage timings
        for stage, data in tele.get("pipeline_timing", {}).items():
            if isinstance(data, dict) and "duration_seconds" in data:
                stage_timings.setdefault(stage, []).append(data["duration_seconds"])

        # Per-stage token usage
        for stage, data in t_efficiency.get("usage_detail", {}).items():
            stage_tokens.setdefault(stage, []).append(data.get("total_tokens", 0))
            model = data.get("model", "")
            if model:
                model_counts[model] = model_counts.get(model, 0) + 1

        # Pipeline total timing
        if "pipeline_total_seconds" in tele.get("pipeline_timing", {}):
            total_pipeline_seconds += tele["pipeline_timing"]["pipeline_total_seconds"]
            timing_samples += 1

    # Compute averages
    avg_score = _safe_div(sum(scores), len(scores))
    avg_token_per_entry = _safe_div(total_tokens, len(scores))
    avg_pipeline_time = _safe_div(total_pipeline_seconds, timing_samples) if timing_samples else 0

    # Per-stage summary
    stage_summary: dict[str, dict] = {}
    for stage, timings in stage_timings.items():
        stage_summary[stage] = {
            "count": len(timings),
            "avg_seconds": round(_safe_div(sum(timings), len(timings)), 1),
            "p50": sorted(timings)[len(timings) // 2] if timings else 0,
        }
    for stage, tokens in stage_tokens.items():
        if stage not in stage_summary:
            stage_summary[stage] = {"count": len(tokens)}
        stage_summary[stage]["avg_tokens"] = round(_safe_div(sum(tokens), len(tokens)))
        stage_summary[stage]["total_tokens"] = sum(tokens)

    return {
        "report_generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "entries_evaluated": len(scores),
        "entries_total": len(results),
        "pass_count": pass_count,
        "pass_rate": f"{pass_count}/{len(scores)}" if scores else "0/0",
        "score_avg": round(avg_score, 1),
        "score_min": min(scores) if scores else 0,
        "score_max": max(scores) if scores else 0,
        "total_tokens_consumed": total_tokens,
        "avg_tokens_per_entry": round(avg_token_per_entry),
        "avg_pipeline_duration_seconds": round(avg_pipeline_time, 1),
        "avg_pipeline_duration_display": _fmt_seconds(avg_pipeline_time),
        "token_quality_efficiency": {
            "avg_tokens_per_quality_point": round(_safe_div(sum(token_quality_list), len(token_quality_list)), 1) if token_quality_list else 0,
            "entries_with_token_data": len(token_quality_list),
        },
        "stage_breakdown": stage_summary,
        "model_usage": dict(sorted(model_counts.items(), key=lambda x: -x[1])),
        "lowest_scores": sorted(
            [(r["entry_id"][:48], r["score"]) for r in results if not r.get("error") and r["score"] <= 70],
            key=lambda x: x[1],
        )[:5],
    }


def _markdown_table(summary: dict) -> str:
    lines = [
        f"# Pipeline Cost-Quality Report — {summary.get('report_generated_at', '?')[:10]}",
        "",
        f"**{summary['entries_evaluated']}** entries evaluated, "
        f"**{summary['pass_count']}** passed (pass rate {summary['pass_rate']})",
        "",
        "## 全局指标",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 平均质量分 | {summary['score_avg']}/100 |",
        f"| 最低分 | {summary['score_min']} |",
        f"| 最高分 | {summary['score_max']} |",
        f"| 总 Token 消耗 | {summary['total_tokens_consumed']:,} |",
        f"| 每题平均 Token | {summary['avg_tokens_per_entry']:,} |",
        f"| 平均管道耗时 | {summary['avg_pipeline_duration_display']} |",
        f"| Token/质量点 | {summary['token_quality_efficiency']['avg_tokens_per_quality_point']} |",
        "",
    ]

    if summary["stage_breakdown"]:
        lines.append("## 分阶段耗时与 Token")
        lines.append("| 阶段 | 平均耗时 | P50 耗时 | 平均 Token | 总 Token | 样本数 |")
        lines.append("|------|---------|---------|-----------|---------|-------|")
        for stage, data in sorted(summary["stage_breakdown"].items()):
            avg_t = data.get("avg_seconds", "—")
            p50 = data.get("p50", "—")
            avg_tok = f"{data.get('avg_tokens', 0):,}" if data.get("avg_tokens") else "—"
            total_tok = f"{data.get('total_tokens', 0):,}" if data.get("total_tokens") else "—"
            if isinstance(avg_t, (int, float)):
                avg_t = _fmt_seconds(avg_t)
            if isinstance(p50, (int, float)):
                p50 = _fmt_seconds(p50)
            lines.append(f"| {stage} | {avg_t} | {p50} | {avg_tok} | {total_tok} | {data['count']} |")
        lines.append("")

    if summary["model_usage"]:
        lines.append("## 模型分布")
        lines.append("| 模型 | 使用次数 |")
        lines.append("|------|---------|")
        for model, count in summary["model_usage"].items():
            lines.append(f"| {model} | {count} |")
        lines.append("")

    if summary["lowest_scores"]:
        lines.append("## 低分条目（≤ 70）")
        lines.append("| 条目 | 分数 |")
        lines.append("|------|------|")
        for name, score in summary["lowest_scores"]:
            lines.append(f"| {name} | {score} |")
        lines.append("")

    lines.append(f"_Generated by pipeline_cost_report.py at {summary['report_generated_at']}_")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry_ids", nargs="*", metavar="ENTRY_ID", help="Limit to specific entries")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown", help="Output format")
    args = parser.parse_args()

    summary = build_summary(args.entry_ids or None)
    if "error" in summary:
        print(summary["error"], file=sys.stderr)
        return 1

    if args.format == "markdown":
        print(_markdown_table(summary))
    else:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
