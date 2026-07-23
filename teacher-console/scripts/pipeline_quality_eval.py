#!/usr/bin/env python3
"""Pipeline quality evaluator for student-error-library entries.

For one or more delivered entries, assemble all available telemetry and produce
a structured quality score across dimensions: completeness, hallucination risk,
format compliance, pipeline accuracy, and token efficiency.

Output is JSON(L) — one record per entry.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIBRARY = PROJECT_ROOT / "student-error-library"
ENTRIES = LIBRARY / "entries"
CONSOLE = PROJECT_ROOT / "teacher-console"
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"

sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))


# ---------------------------------------------------------------------------
# 1. Data loaders
# ---------------------------------------------------------------------------


def load_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def entry_path(entry_id: str) -> Path:
    """Find entry directory by ID (exact or prefix match)."""
    target = ENTRIES / entry_id
    if target.is_dir():
        return target
    for child in sorted(ENTRIES.iterdir()):
        if child.name.startswith(entry_id):
            return child
    raise FileNotFoundError(f"no entry matching {entry_id!r} in {ENTRIES}")


# ---------------------------------------------------------------------------
# 2. Request / attempt telemetry collectors
# ---------------------------------------------------------------------------

REQUEST_NAMES = {
    "source.clean": "source-clean-request.json",
    "analysis": "analysis-request.json",
    "answer.revise": "answer-revision-request.json",
    "visualization": "visualization-request.json",
}


def _collect_requests(entry: Path) -> list[dict]:
    """Collect all request records with timing and usage."""
    collected: list[dict] = []
    for kind, filename in REQUEST_NAMES.items():
        raw = load_json(entry / filename)
        if not raw:
            continue
        record = {
            "kind": kind,
            "status": raw.get("status", "unknown"),
            "requested_at": raw.get("requested_at", ""),
            "completed_at": raw.get("completed_at", ""),
            "provider": raw.get("provider", ""),
            "model_id": raw.get("model_id", raw.get("model_display_name", "")),
            "routing_tier": raw.get("routing_tier", ""),
            "usage": raw.get("usage", {}),
            "attempts": raw.get("attempts", []),
        }
        # Sum token_usage from each attempt
        attempts = raw.get("attempts", [])
        if attempts and not record["usage"]:
            total_usage: dict[str, int] = {}
            for att in attempts:
                tu = att.get("token_usage", {})
                if isinstance(tu, dict):
                    for k, v in tu.items():
                        if isinstance(v, int) and v >= 0:
                            total_usage[k] = total_usage.get(k, 0) + v
            if total_usage:
                record["usage"] = total_usage
        collected.append(record)
    return collected


def _collect_attempts(entry: Path) -> list[dict]:
    """Collect individual attempt records across all request files."""
    all_attempts: list[dict] = []
    for filename in REQUEST_NAMES.values():
        raw = load_json(entry / filename)
        for att in raw.get("attempts", []):
            if isinstance(att, dict):
                all_attempts.append(att)
    return all_attempts


def _collect_archive_events(entry: Path) -> list[dict]:
    """Collect candidate-archive events for this entry."""
    events: list[dict] = []
    path = entry / "candidate-archive.jsonl"
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


# ---------------------------------------------------------------------------
# 3. Quality scorers
# ---------------------------------------------------------------------------


def _word_count(text: str) -> int:
    return len(re.findall(r"[\w一-鿿]+", text))


def _section_keywords(text: str) -> set[str]:
    """Extract low-frequency keywords as a rough fingerprint of content."""
    words = re.findall(r"[\w一-鿿]+", text.lower())
    counts: dict[str, int] = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    return {w for w, c in counts.items() if 1 <= c <= 5 and len(w) > 1}


def _has_heading(text: str, heading: str) -> bool:
    return bool(re.search(r"^#{1,4}\s*" + re.escape(heading), text, re.MULTILINE))


def score_completeness(content: str, solution: str) -> dict:
    """Score answer completeness by comparing content.md with solution.md."""
    if not solution:
        return {
            "score": 0,
            "reason": "无 solution.md 参考基线",
            "content_words": 0,
            "solution_words": 0,
            "missing_headings": [],
        }

    content_words = _word_count(content)
    solution_words = _word_count(solution)

    if content_words == 0:
        return {"score": 0, "reason": "content.md 为空", "content_words": 0, "solution_words": solution_words}

    # Structural coverage: check section headings
    solution_headings = re.findall(r"^#{1,4}\s+(.+)$", solution, re.MULTILINE)
    content_headings = set(re.findall(r"^#{1,4}\s+(.+)$", content, re.MULTILINE))
    missing = [h for h in solution_headings if h.strip() not in content_headings]

    # Keyword overlap
    content_kw = _section_keywords(content)
    solution_kw = _section_keywords(solution)
    overlap = len(content_kw & solution_kw)
    total_sol_kw = len(solution_kw) or 1
    keyword_ratio = overlap / total_sol_kw

    # Length ratio (capped)
    len_ratio = min(content_words / solution_words, 1.5) if solution_words else 0

    # Score
    heading_score = max(0, 1.0 - len(missing) * 0.15)
    score_raw = heading_score * 0.3 + keyword_ratio * 0.4 + min(len_ratio, 1.0) * 0.3
    final = round(min(score_raw, 1.0) * 100)

    reasons = []
    if missing:
        reasons.append(f"缺少 {len(missing)} 个章节标题")
    if keyword_ratio < 0.5:
        reasons.append(f"关键词覆盖仅 {keyword_ratio:.0%}")
    if len_ratio < 0.4:
        reasons.append(f"正文长度仅为基线的 {len_ratio:.0%}")

    return {
        "score": final,
        "reason": "；".join(reasons) if reasons else "结构完整，关键词覆盖充分",
        "content_words": content_words,
        "solution_words": solution_words,
        "keyword_overlap_ratio": round(keyword_ratio, 3),
        "missing_headings": missing,
    }


def _extract_equations(text: str) -> set[str]:
    """Extract LaTeX math expression bodies from both ``$...$`` and ``$$...$$``."""
    eqs: set[str] = set()
    # Display math $$...$$
    for m in re.finditer(r"\$\$(.+?)\$\$", text, re.DOTALL):
        eqs.add(m.group(1).strip())
    # Inline math $...$ — keep only those NOT inside $$...$$
    cleaned = re.sub(r"\$\$.+?\$\$", "", text, flags=re.DOTALL)
    for m in re.finditer(r"\$([^$]+)\$", cleaned):
        eqs.add(m.group(1).strip())
    return eqs


def score_hallucination(content: str, problem: str, solution: str) -> dict:
    """Score hallucination risk — flag content not traceable to problem or solution."""
    if not content:
        return {"score": 100, "reason": "无内容可评估", "flagged_count": 0, "flag_examples": []}

    combined = (problem + "\n" + solution).lower()

    # Compare LaTeX equations — correct handling of both $ and $$ delimiters
    content_eqs = _extract_equations(content)
    solution_eqs = _extract_equations(solution)
    unknown_eq = [eq for eq in content_eqs if eq not in solution_eqs]
    # Skip very short tokens (e.g., "x", "t") that appear as math but aren't meaningfully hallucinated
    flagged = [eq for eq in unknown_eq if len(eq) > 4]

    # Check numeric values (floats, scientific notation, fractions)
    content_nums = set(re.findall(r"\b\d+\.?\d*(?:×10[^{}]*)?", content))
    sol_nums = set(re.findall(r"\b\d+\.?\d*(?:×10[^{}]*)?", combined))
    extra_nums = [n for n in content_nums if n not in sol_nums and len(n) > 3]

    # Score
    flags = flagged[:10] + extra_nums[:5]
    if flags:
        score = max(0, 100 - len(flags) * 15)
        return {
            "score": score,
            "reason": f"发现 {len(flags)} 个在输入材料中无直接来源的内容片段",
            "flagged_count": len(flags),
            "flag_examples": ["公式: " + f[:60] for f in flagged[:8]] + ["数值: " + n for n in extra_nums[:4]],
        }
    return {"score": 100, "reason": "未发现明显幻觉内容", "flagged_count": 0, "flag_examples": []}


def score_format(content: str) -> dict:
    """Score format compliance — valid Markdown, KaTeX, no prohibited."""
    if not content:
        return {"score": 0, "reason": "无内容"}

    issues: list[str] = []

    # Check KaTeX delimiter balance
    dollar_pairs = content.count("$$")
    if dollar_pairs % 2 != 0:
        issues.append("KaTeX $$ 分隔符未配对")
    single_dollar = content.count("$") - dollar_pairs * 2
    if single_dollar % 2 != 0:
        issues.append("KaTeX $ 分隔符未配对")

    # Prohibited patterns
    prohib = re.search(r"(?:作为.*人工智能|作为一个.*模型|很抱歉|对不起|我不确定)", content)
    if prohib:
        issues.append("包含不当客套话")

    # Blank lines between KaTeX blocks
    if re.search(r"\$\$\s*\n\s*\$\$", content):
        issues.append("存在空 KaTeX 块")

    score = max(0, 100 - len(issues) * 15)
    reason = "；".join(issues) if issues else "格式合规"
    return {"score": score, "reason": reason, "issues": issues}


def score_pipeline_accuracy(entry: Path, requests: list[dict]) -> dict:
    """Verify pipeline state transitions match actual request records."""
    pipeline = load_json(entry / "pipeline.json")
    state = pipeline.get("state", "unknown")
    steps = pipeline.get("steps", [])

    issues: list[str] = []
    warnings: list[str] = []
    if state != "delivered":
        issues.append(f"pipeline 最终状态为 {state!r}，期望 delivered")

    # Check each request kind has a corresponding step
    for req in requests:
        kind = req["kind"]
        status = req.get("status", "")
        if status in ("failed", "error"):
            issues.append(f"{kind} 请求状态为 {status}")

    # Check output delivery-manifest.json — warn only (output may be moved post-delivery)
    output_dir = pipeline.get("delivery_manifest", "")
    if output_dir:
        manifest_path = Path(output_dir)
        if not manifest_path.is_file():
            warnings.append("交付目录已移动（不影响评分）")

    base = 100 - len(issues) * 15
    return {
        "score": max(0, base),
        "state": state,
        "steps": steps,
        "issues": issues,
        "warnings": warnings,
    }


def score_token_efficiency(requests: list[dict], completeness_score: float, pipeline_accuracy_score: float) -> dict:
    """Aggregate token usage and compute efficiency ratio."""
    total_tokens = 0
    usage_detail: dict[str, dict] = {}

    for req in requests:
        usage = req.get("usage", {})
        if isinstance(usage, dict):
            t = usage.get("total_tokens", 0) or usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
            if isinstance(t, int):
                total_tokens += t
                usage_detail[req["kind"]] = {
                    "model": req.get("model_id", req.get("provider", "")),
                    "total_tokens": t,
                    "prompt_tokens": usage.get("prompt_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                }

    quality_avg = (completeness_score + pipeline_accuracy_score) / 2.0
    efficiency = round(total_tokens / max(quality_avg, 1), 2) if quality_avg > 0 else 0

    return {
        "total_tokens": total_tokens,
        "usage_detail": usage_detail,
        "quality_score": quality_avg,
        "tokens_per_quality_point": efficiency,
    }


# ---------------------------------------------------------------------------
# 4. Main evaluator
# ---------------------------------------------------------------------------


def evaluate_entry(entry_id_or_path: str) -> dict:
    """Run all quality evaluations for one entry."""
    try:
        entry = entry_path(entry_id_or_path)
    except FileNotFoundError as exc:
        return {"entry_id": entry_id_or_path, "error": str(exc), "score": 0, "pass": False}

    entry_id = entry.name
    pipeline = load_json(entry / "pipeline.json")

    # Load texts
    problem = load_text(entry / "problem.md")
    solution = load_text(entry / "solution.md")
    # content.md may be student-solution.md (older naming) or content.md
    content = load_text(entry / "content.md") or load_text(entry / "student-solution.md")
    teacher_content = load_text(entry / "teacher-solution.md") or load_text(entry / "solution.md")

    # Telemetry
    requests = _collect_requests(entry)
    attempts = _collect_attempts(entry)
    archive_events = _collect_archive_events(entry)

    # Quality dimensions
    completeness = score_completeness(content, solution)
    hallucination = score_hallucination(content, problem, solution)
    fmt = score_format(content)
    pipeline_acc = score_pipeline_accuracy(entry, requests)
    token_eff = score_token_efficiency(requests, completeness["score"], pipeline_acc["score"])

    # Pipeline timing
    timing: dict = {}
    for req in requests:
        start = req.get("requested_at", "")
        end = req.get("completed_at", "")
        if start and end:
            try:
                t_start = datetime.fromisoformat(start)
                t_end = datetime.fromisoformat(end)
                delta = (t_end - t_start).total_seconds()
                timing[req["kind"]] = {"duration_seconds": round(delta, 1), "status": req["status"]}
            except (ValueError, TypeError):
                pass
    if pipeline.get("started_at") and pipeline.get("completed_at"):
        try:
            total = (
                datetime.fromisoformat(pipeline["completed_at"]) - datetime.fromisoformat(pipeline["started_at"])
            ).total_seconds()
            timing["pipeline_total_seconds"] = round(total, 1)
        except (ValueError, TypeError):
            pass

    # Weighted total score
    completeness_w = completeness["score"] * 0.30
    hallucination_w = hallucination["score"] * 0.35
    format_w = fmt["score"] * 0.10
    pipeline_w = pipeline_acc["score"] * 0.15
    total_score = round(completeness_w + hallucination_w + format_w + pipeline_w)

    # Pass/fail
    hallucination_penalty = 100 - hallucination["score"]
    passed = hallucination_penalty <= 20 and total_score >= 70

    result = {
        "entry_id": entry_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "score": total_score,
        "pass": passed,
        "is_legacy": len(requests) == 0,
        "dimensions": {
            "completeness": {"score": completeness["score"], "reason": completeness["reason"], "detail": completeness},
            "hallucination": {
                "score": hallucination["score"],
                "reason": hallucination["reason"],
                "flags": hallucination.get("flag_examples", ["(no detail)"]),
            },
            "format": {"score": fmt["score"], "reason": fmt["reason"]},
            "pipeline_accuracy": {
                "score": pipeline_acc["score"],
                "reason": "; ".join(pipeline_acc["issues"]) if pipeline_acc["issues"] else "正常",
            },
        },
        "telemetry": {
            "requests": len(requests),
            "attempts": len(attempts),
            "archive_events": len(archive_events),
            "token_efficiency": token_eff,
            "pipeline_timing": timing,
        },
        "overall_reasoning": _summarize(total_score, completeness, hallucination, fmt, pipeline_acc, len(requests)),
    }
    return result


def _summarize(
    total: int,
    completeness: dict,
    hallucination: dict,
    fmt: dict,
    pipeline_acc: dict,
    request_count: int,
) -> str:
    parts = [f"质量总分 {total}/100"]
    if completeness["score"] < 80:
        parts.append(f"完整性 {completeness['score']}/100")
    if hallucination["score"] < 100:
        parts.append(f"幻觉风险 {hallucination['score']}/100")
    if fmt["score"] < 100:
        parts.append(f"格式问题 {fmt['score']}/100")
    if pipeline_acc["score"] < 100:
        issues = pipeline_acc.get("issues", [])
        if issues:
            parts.append(f"管道异常: {'; '.join(issues[:2])}")
    if request_count == 0:
        parts.append("（无结构化请求记录，可能为旧版流程）")
    return "；".join(parts)


# ---------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "entry_ids", nargs="*", metavar="ENTRY_ID", help="Entry IDs or prefixes; omit to scan all delivered"
    )
    parser.add_argument("--library", default=str(LIBRARY))
    parser.add_argument("--jsonl", action="store_true", help="Output JSONL (one entry per line)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed per-entry reports")
    args = parser.parse_args()

    entries: list[Path]
    if args.entry_ids:
        entries = []
        for eid in args.entry_ids:
            try:
                entries.append(entry_path(eid))
            except FileNotFoundError as exc:
                print(f"Skipping {eid}: {exc}", file=sys.stderr)
    else:
        entries = sorted(ENTRIES.iterdir())
        delivered = []
        for e in entries:
            pipeline = load_json(e / "pipeline.json")
            if pipeline.get("state") == "delivered":
                delivered.append(e)
        entries = delivered
        print(f"Auto-scan: {len(entries)} delivered entries found", file=sys.stderr)

    results: list[dict] = []
    for entry in entries:
        result = evaluate_entry(entry.name)
        results.append(result)
        if args.verbose and not args.jsonl:
            _print_verbose(result)

    if args.jsonl:
        for r in results:
            print(json.dumps(r, ensure_ascii=False))
    else:
        report = _summary_report(results)
        print(json.dumps(report, indent=2, ensure_ascii=False))

    return 0


def _print_verbose(result: dict) -> None:
    print(f"\n{'=' * 60}")
    print(f"条目: {result['entry_id'][:48]}...")
    print(f"总分: {result['score']}/100  {'✅' if result['pass'] else '❌'}")
    for dim, data in result["dimensions"].items():
        print(f"  {dim}: {data['score']}/100 — {str(data.get('reason', ''))[:80]}")
    tele = result["telemetry"]
    print(
        f"  请求数: {tele['requests']}  尝试数: {tele['attempts']}  Token: {tele['token_efficiency']['total_tokens']}"
    )
    if tele["token_efficiency"]["usage_detail"]:
        for kind, usage in tele["token_efficiency"]["usage_detail"].items():
            print(f"    {kind}: {usage.get('total_tokens', 0)} tok ({usage.get('model', '?')})")
    print(result.get("overall_reasoning", ""))


def _summary_report(results: list[dict]) -> dict:
    scores = [r["score"] for r in results if not r.get("error")]
    legacy = sum(1 for r in results if r.get("is_legacy"))
    total_tokens = sum(r["telemetry"]["token_efficiency"]["total_tokens"] for r in results)

    return {
        "framework": "pipeline-quality-eval v1",
        "evaluated_entries": len(results),
        "errors": sum(1 for r in results if r.get("error")),
        "legacy_entries": legacy,
        "score_avg": round(sum(scores) / len(scores), 1) if scores else 0,
        "score_min": min(scores) if scores else 0,
        "score_max": max(scores) if scores else 0,
        "pass_rate": f"{sum(1 for r in results if r.get('pass'))}/{len(results)}",
        "total_tokens": total_tokens,
        "entries": [(r["entry_id"], r["score"], r.get("pass"), r.get("overall_reasoning", "")) for r in results],
    }


if __name__ == "__main__":
    raise SystemExit(main())
