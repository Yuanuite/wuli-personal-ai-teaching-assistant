#!/usr/bin/env python3
"""Build and measure a teacher-reviewed retrieval evaluation set.

The benchmark never calls an LLM and never changes retrieval policy.  ``seed``
creates machine-proposed draft cases from canonical entry metadata; a teacher
must change ``review_status`` to ``approved`` before the cases count as a fixed
evaluation set.  ``run`` is read-only unless ``--record`` is explicitly used.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import candidate_archive  # noqa: E402
import kb  # noqa: E402
import knowledge_store  # noqa: E402


SCHEMA_VERSION = 1
DEFAULT_CASES = Path("evals") / "retrieval-cases.jsonl"
CATEGORIES = {"knowledge_point", "problem_type", "error_type", "teacher_phrase"}
REVIEW_STATUSES = {"draft", "approved", "rejected"}


def default_cases_path(library: Path) -> Path:
    return library / DEFAULT_CASES


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number}: case must be a JSON object")
        value["_line"] = line_number
        cases.append(value)
    return cases


def validate_cases(library: Path, cases: list[dict[str, Any]]) -> dict[str, Any]:
    entry_ids = {entry.name for entry in kb.entry_dirs(library)}
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    status_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    for index, case in enumerate(cases, 1):
        line = case.get("_line", index)
        case_id = str(case.get("id", "")).strip()
        query = str(case.get("query", "")).strip()
        category = str(case.get("category", "")).strip()
        review_status = str(case.get("review_status", "draft")).strip()
        relevant = case.get("relevant_entry_ids")
        if not case_id:
            errors.append(f"line {line}: missing id")
        elif case_id in seen:
            errors.append(f"line {line}: duplicate id {case_id}")
        seen.add(case_id)
        if not query:
            errors.append(f"line {line}: missing query")
        if category not in CATEGORIES:
            errors.append(f"line {line}: invalid category {category or '<empty>'}")
        else:
            category_counts[category] += 1
        if review_status not in REVIEW_STATUSES:
            errors.append(f"line {line}: invalid review_status {review_status or '<empty>'}")
        else:
            status_counts[review_status] += 1
        if not isinstance(relevant, list) or not relevant or not all(isinstance(item, str) and item for item in relevant):
            errors.append(f"line {line}: relevant_entry_ids must be a non-empty string list")
        else:
            missing = sorted(set(relevant) - entry_ids)
            if missing:
                errors.append(f"line {line}: unknown relevant entries: {', '.join(missing)}")
    approved = status_counts.get("approved", 0)
    if approved < 30:
        warnings.append(f"fixed set needs at least 30 approved cases; current approved={approved}")
    missing_categories = sorted(CATEGORIES - set(category_counts))
    if missing_categories:
        warnings.append(f"categories not covered: {', '.join(missing_categories)}")
    return {
        "schema_version": SCHEMA_VERSION,
        "valid": not errors,
        "case_count": len(cases),
        "status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "errors": errors,
        "warnings": warnings,
    }


def _record_values(record: dict[str, Any], key: str) -> list[str]:
    value = record.get(key, [])
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def seed_cases(library: Path, *, limit: int = 30) -> list[dict[str, Any]]:
    """Create deterministic draft labels; these are not teacher-approved."""
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in kb.entry_dirs(library):
        record = kb.load_json(entry / "record.json", {}) or {}
        if str(record.get("status", "")) not in {"ready", "delivered"}:
            continue
        title = str(record.get("title", entry.name)).strip()
        knowledge = _record_values(record, "knowledge_points")
        errors = [item for item in _record_values(record, "error_types") if item != "待确认"]
        base = {"relevant_entry_ids": [entry.name], "review_status": "draft", "notes": "机器按条目元数据生成；请教师核对查询表达与相关条目后改为 approved。"}
        if title:
            pools["problem_type"].append({**base, "query": title})
        if knowledge:
            pools["knowledge_point"].append({**base, "query": " ".join(knowledge[:2])})
        if errors:
            pools["error_type"].append({**base, "query": f"{errors[0]} 容易错的题 {knowledge[0] if knowledge else title}"})
        if knowledge or errors:
            phrase_parts = ["帮我找一道"]
            if errors:
                phrase_parts.append(f"容易出现{errors[0]}的")
            phrase_parts.append(f"{knowledge[0] if knowledge else title}题")
            pools["teacher_phrase"].append({**base, "query": "".join(phrase_parts)})

    selected: list[dict[str, Any]] = []
    category_order = ("knowledge_point", "problem_type", "error_type", "teacher_phrase")
    offset = 0
    while len(selected) < limit and any(pools.values()):
        category = category_order[offset % len(category_order)]
        if pools[category]:
            selected.append({"schema_version": SCHEMA_VERSION, **pools[category].pop(0), "category": category})
        offset += 1
    for index, case in enumerate(selected, 1):
        case["id"] = f"retrieval-{index:03d}"
    return selected


def write_cases(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for case in cases:
        value = {key: item for key, item in case.items() if not key.startswith("_")}
        lines.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _metrics(items: list[dict[str, Any]], top_k: int) -> dict[str, Any]:
    cutoffs = sorted({1, 3, 5, top_k})
    return {
        "count": len(items),
        "empty_rate": _mean([1.0 if not item["retrieved_entry_ids"] else 0.0 for item in items]),
        "mrr": _mean([item["reciprocal_rank"] for item in items]),
        "hit_rate": {f"@{cutoff}": _mean([item["hits"][cutoff] for item in items]) for cutoff in cutoffs},
        "recall": {f"@{cutoff}": _mean([item["recall"][cutoff] for item in items]) for cutoff in cutoffs},
    }


def run_benchmark(
    library: Path,
    cases: list[dict[str, Any]],
    *,
    top_k: int = 5,
    include_draft: bool = False,
) -> dict[str, Any]:
    top_k = max(5, int(top_k))
    validation = validate_cases(library, cases)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    eligible = [
        case for case in cases
        if case.get("review_status") == "approved" or (include_draft and case.get("review_status") == "draft")
    ]
    per_case: list[dict[str, Any]] = []
    for case in eligible:
        evidence = knowledge_store.query(library, str(case["query"]), mode="teaching", top_k=top_k)
        retrieved = [str(item.get("entry_id", "")) for item in evidence.get("results", []) if item.get("entry_id")]
        relevant = set(case["relevant_entry_ids"])
        ranks = [index + 1 for index, entry_id in enumerate(retrieved) if entry_id in relevant]
        cutoffs = sorted({1, 3, 5, top_k})
        per_case.append({
            "id": str(case["id"]),
            "query": str(case["query"]),
            "category": str(case["category"]),
            "review_status": str(case.get("review_status", "draft")),
            "relevant_entry_ids": sorted(relevant),
            "retrieved_entry_ids": retrieved,
            "first_relevant_rank": min(ranks) if ranks else None,
            "reciprocal_rank": round(1.0 / min(ranks), 4) if ranks else 0.0,
            "hits": {cutoff: 1.0 if any(entry_id in relevant for entry_id in retrieved[:cutoff]) else 0.0 for cutoff in cutoffs},
            "recall": {cutoff: round(len(relevant & set(retrieved[:cutoff])) / len(relevant), 4) for cutoff in cutoffs},
        })
    overall = _metrics(per_case, top_k)
    categories = {
        category: _metrics([item for item in per_case if item["category"] == category], top_k)
        for category in sorted({item["category"] for item in per_case})
    }
    approved = [case for case in eligible if case.get("review_status") == "approved"]
    approved_count = len(approved)
    approved_categories = {str(case.get("category", "")) for case in approved}
    fixed_set_ready = approved_count >= 30 and CATEGORIES.issubset(approved_categories) and not include_draft
    recall_at_5 = overall["recall"].get("@5", 0.0)
    teacher_phrase_miss = 1.0 - categories.get("teacher_phrase", {}).get("hit_rate", {}).get("@5", 0.0)
    threshold_evaluable = fixed_set_ready and categories.get("teacher_phrase", {}).get("count", 0) > 0
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "retrieval-fixed-set-benchmark",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "top_k": top_k,
        "include_draft": include_draft,
        "fixed_set_ready": fixed_set_ready,
        "threshold_evaluable": threshold_evaluable,
        "upgrade_recommended": bool(threshold_evaluable and (recall_at_5 < 0.85 or teacher_phrase_miss > 0.15)),
        "validation": validation,
        "eligible_cases": len(eligible),
        "excluded_cases": len(cases) - len(eligible),
        "overall": overall,
        "by_category": categories,
        "missed_case_ids_at_5": [item["id"] for item in per_case if not item["hits"].get(5, 0.0)],
        "per_case": per_case,
        "notes": [
            "Draft cases are exploratory and cannot trigger retrieval-policy changes.",
            "Backend enhancement requires at least 30 teacher-approved cases.",
            "The approved fixed set must cover knowledge point, problem type, error type, and teacher phrasing.",
            "The benchmark calls no model and changes no retrieval policy.",
        ],
    }


def record_report(library: Path, report: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    compact = {key: value for key, value in report.items() if key != "per_case"}
    event = candidate_archive.append_library_event(
        library,
        task_type="evolve.observation.retrieval",
        actor="system",
        event_type="retrieval-benchmark",
        status="completed",
        summary=(
            f"Retrieval benchmark: approved={report['validation']['status_counts'].get('approved', 0)}, "
            f"recall@5={report['overall']['recall'].get('@5', 0.0)}, ready={report['fixed_set_ready']}"
        ),
        request=request,
        result=compact,
    )
    try:
        event["knowledge_store"] = knowledge_store.rebuild(library)
    except Exception as exc:  # noqa: BLE001
        event["knowledge_store"] = {"status": "skipped", "error": str(exc)}
    return event


def print_markdown(report: dict[str, Any]) -> None:
    print("# Retrieval Fixed-set Benchmark")
    print()
    print(f"- eligible: {report['eligible_cases']}; excluded: {report['excluded_cases']}")
    print(f"- fixed_set_ready: {report['fixed_set_ready']}; threshold_evaluable: {report['threshold_evaluable']}")
    print(f"- upgrade_recommended: {report['upgrade_recommended']}")
    print(f"- Recall@5: {report['overall']['recall'].get('@5', 0.0)}; MRR: {report['overall']['mrr']}; empty_rate: {report['overall']['empty_rate']}")
    print()
    print("| category | n | Hit@5 | Recall@5 | MRR | empty |")
    print("|---|---:|---:|---:|---:|---:|")
    for category, metrics in report["by_category"].items():
        print(f"| {category} | {metrics['count']} | {metrics['hit_rate'].get('@5', 0.0)} | {metrics['recall'].get('@5', 0.0)} | {metrics['mrr']} | {metrics['empty_rate']} |")
    if report["missed_case_ids_at_5"]:
        print()
        print("Missed at 5: " + ", ".join(report["missed_case_ids_at_5"]))


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    value.add_argument("--cases", type=Path)
    sub = value.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed", help="create machine-proposed draft cases")
    seed.add_argument("--limit", type=int, default=30)
    seed.add_argument("--force", action="store_true", help="replace an existing local case file")
    sub.add_parser("validate", help="validate case schema and entry references")
    run = sub.add_parser("run", help="run the fixed-set retrieval benchmark")
    run.add_argument("--top-k", type=int, default=5)
    run.add_argument("--include-draft", action="store_true")
    run.add_argument("--format", choices=("json", "markdown"), default="json")
    run.add_argument("--record", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    library = args.library.expanduser().resolve()
    cases_path = args.cases.expanduser().resolve() if args.cases else default_cases_path(library)
    if args.command == "seed":
        if cases_path.exists() and not args.force:
            raise FileExistsError(f"case file already exists: {cases_path}; use --force to replace it")
        cases = seed_cases(library, limit=max(1, args.limit))
        write_cases(cases_path, cases)
        print(json.dumps({"status": "seeded", "path": str(cases_path), "case_count": len(cases), "review_status": "draft"}, ensure_ascii=False, indent=2))
        return 0
    cases = load_cases(cases_path)
    if args.command == "validate":
        result = validate_cases(library, cases)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["valid"] else 2
    report = run_benchmark(library, cases, top_k=max(1, args.top_k), include_draft=args.include_draft)
    if args.record:
        if not report["fixed_set_ready"]:
            raise ValueError("--record requires at least 30 teacher-approved cases; draft results are not durable evidence")
        report["archive_event"] = record_report(library, report, {"case_count": report["eligible_cases"], "top_k": report["top_k"]})
    if args.format == "markdown":
        print_markdown(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
