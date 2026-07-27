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
from itertools import combinations
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import candidate_archive  # noqa: E402
import kb  # noqa: E402
import knowledge_store  # noqa: E402

SCHEMA_VERSION = 2
DEFAULT_CASES = Path("evals") / "retrieval-cases.jsonl"
CATEGORIES = {"knowledge_point", "problem_type", "error_type", "teacher_phrase"}
REVIEW_STATUSES = {"draft", "approved", "rejected"}
EVALUATION_SPLITS = {"calibration", "holdout"}
CALIBRATION_MIN_CASES = 30
HOLDOUT_MIN_CASES = 12


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
    query_splits: dict[str, set[str]] = defaultdict(set)
    status_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    approved_split_categories: dict[str, set[str]] = defaultdict(set)
    for index, case in enumerate(cases, 1):
        line = case.get("_line", index)
        case_id = str(case.get("id", "")).strip()
        query = str(case.get("query", "")).strip()
        category = str(case.get("category", "")).strip()
        review_status = str(case.get("review_status", "draft")).strip()
        evaluation_split = str(case.get("evaluation_split", "calibration")).strip()
        relevant = case.get("relevant_entry_ids")
        if not case_id:
            errors.append(f"line {line}: missing id")
        elif case_id in seen:
            errors.append(f"line {line}: duplicate id {case_id}")
        seen.add(case_id)
        if not query:
            errors.append(f"line {line}: missing query")
        elif evaluation_split in EVALUATION_SPLITS:
            query_splits[query].add(evaluation_split)
        if category not in CATEGORIES:
            errors.append(f"line {line}: invalid category {category or '<empty>'}")
        else:
            category_counts[category] += 1
        if review_status not in REVIEW_STATUSES:
            errors.append(f"line {line}: invalid review_status {review_status or '<empty>'}")
        else:
            status_counts[review_status] += 1
        if evaluation_split not in EVALUATION_SPLITS:
            errors.append(f"line {line}: invalid evaluation_split {evaluation_split or '<empty>'}")
        else:
            split_counts[evaluation_split] += 1
            if review_status == "approved" and category in CATEGORIES:
                approved_split_categories[evaluation_split].add(category)
        if (
            not isinstance(relevant, list)
            or not relevant
            or not all(isinstance(item, str) and item for item in relevant)
        ):
            errors.append(f"line {line}: relevant_entry_ids must be a non-empty string list")
        else:
            missing = sorted(set(relevant) - entry_ids)
            if missing:
                errors.append(f"line {line}: unknown relevant entries: {', '.join(missing)}")
    approved_by_split = Counter(
        str(case.get("evaluation_split", "calibration"))
        for case in cases
        if case.get("review_status") == "approved"
    )
    calibration_approved = approved_by_split.get("calibration", 0)
    holdout_approved = approved_by_split.get("holdout", 0)
    if calibration_approved < CALIBRATION_MIN_CASES:
        warnings.append(
            f"calibration set needs at least {CALIBRATION_MIN_CASES} approved cases; "
            f"current approved={calibration_approved}"
        )
    if holdout_approved < HOLDOUT_MIN_CASES:
        warnings.append(
            f"independent holdout needs at least {HOLDOUT_MIN_CASES} approved cases; "
            f"current approved={holdout_approved}"
        )
    missing_categories = sorted(CATEGORIES - set(category_counts))
    if missing_categories:
        warnings.append(f"categories not covered: {', '.join(missing_categories)}")
    overlapping_queries = sorted(
        query for query, splits in query_splits.items() if len(splits) > 1
    )
    if overlapping_queries:
        errors.append(
            "holdout queries must not duplicate calibration queries: "
            + ", ".join(overlapping_queries[:5])
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "valid": not errors,
        "case_count": len(cases),
        "status_counts": dict(sorted(status_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "approved_split_counts": dict(sorted(approved_by_split.items())),
        "approved_split_categories": {
            split: sorted(categories)
            for split, categories in sorted(approved_split_categories.items())
        },
        "errors": errors,
        "warnings": warnings,
    }


def _record_values(record: dict[str, Any], key: str) -> list[str]:
    value = record.get(key, [])
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def seed_cases(
    library: Path,
    *,
    limit: int = 30,
    evaluation_split: str = "calibration",
    batch_id: str = "",
    excluded_entry_ids: set[str] | None = None,
    excluded_queries: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Create deterministic draft labels; these are not teacher-approved."""
    if evaluation_split not in EVALUATION_SPLITS:
        raise ValueError(f"unsupported evaluation split: {evaluation_split}")
    excluded_entry_ids = excluded_entry_ids or set()
    excluded_queries = excluded_queries or set()
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in kb.entry_dirs(library):
        if entry.name in excluded_entry_ids:
            continue
        record = kb.load_json(entry / "record.json", {}) or {}
        if str(record.get("status", "")) not in {"ready", "delivered"}:
            continue
        title = str(record.get("title", entry.name)).strip()
        knowledge = _record_values(record, "knowledge_points")
        errors = [item for item in _record_values(record, "error_types") if item != "待确认"]
        base = {
            "relevant_entry_ids": [entry.name],
            "review_status": "draft",
            "evaluation_split": evaluation_split,
            "batch_id": batch_id or f"{evaluation_split}-draft",
            "notes": "机器按条目元数据生成；请教师核对查询表达与相关条目后改为 approved。",
        }
        if title:
            if title not in excluded_queries:
                pools["problem_type"].append({**base, "query": title})
        if knowledge:
            knowledge_query = " ".join(knowledge[:2])
            if knowledge_query not in excluded_queries:
                pools["knowledge_point"].append({**base, "query": knowledge_query})
        if errors:
            error_query = f"{errors[0]} 容易错的题 {knowledge[0] if knowledge else title}"
            if error_query not in excluded_queries:
                pools["error_type"].append({**base, "query": error_query})
        if knowledge or errors:
            phrase_parts = ["帮我找一道"]
            if errors:
                phrase_parts.append(f"容易出现{errors[0]}的")
            phrase_parts.append(f"{knowledge[0] if knowledge else title}题")
            teacher_query = "".join(phrase_parts)
            if teacher_query not in excluded_queries:
                pools["teacher_phrase"].append({**base, "query": teacher_query})

    selected: list[dict[str, Any]] = []
    category_order = ("knowledge_point", "problem_type", "error_type", "teacher_phrase")
    offset = 0
    while len(selected) < limit and any(pools.values()):
        category = category_order[offset % len(category_order)]
        if pools[category]:
            selected.append({"schema_version": SCHEMA_VERSION, **pools[category].pop(0), "category": category})
        offset += 1
    for index, case in enumerate(selected, 1):
        prefix = "holdout" if evaluation_split == "holdout" else "retrieval"
        case["id"] = f"{prefix}-{index:03d}"
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
    ranking_policy: str = "baseline",
    evaluation_split: str = "all",
    evidence_selection_policy: str = "baseline",
) -> dict[str, Any]:
    top_k = max(5, int(top_k))
    validation = validate_cases(library, cases)
    if not validation["valid"]:
        raise ValueError("; ".join(validation["errors"]))
    if evaluation_split not in {*EVALUATION_SPLITS, "all"}:
        raise ValueError(f"unsupported evaluation split: {evaluation_split}")
    approved_or_draft = [
        case
        for case in cases
        if case.get("review_status") == "approved" or (include_draft and case.get("review_status") == "draft")
    ]
    eligible = [
        case
        for case in approved_or_draft
        if evaluation_split == "all"
        or str(case.get("evaluation_split", "calibration")) == evaluation_split
    ]
    per_case: list[dict[str, Any]] = []
    for case in eligible:
        evidence = knowledge_store.query(
            library,
            str(case["query"]),
            mode="teaching",
            top_k=top_k,
            ranking_policy=ranking_policy,
        )
        result_items = list(evidence.get("results", []))
        retrieved = [str(item.get("entry_id", "")) for item in result_items if item.get("entry_id")]
        accepted_retrieved = [
            str(item.get("entry_id", ""))
            for item in evidence.get("results", [])
            if item.get("entry_id")
            and item.get("evidence_audit", {}).get("decision") == "accepted"
        ]
        selection = knowledge_store.select_evidence_results(
            result_items,
            selection_policy=evidence_selection_policy,
            limit=top_k,
        )
        selected_results = selection["selected_results"]
        selected_retrieved = [
            str(item.get("entry_id", ""))
            for item in selected_results
            if item.get("entry_id")
        ]
        selected_duplicate_pairs = sum(
            1
            for left, right in combinations(selected_results, 2)
            if knowledge_store._evidence_results_duplicate(left, right)
        )
        selected_conflict_pairs = sum(
            1
            for left, right in combinations(selected_results, 2)
            if knowledge_store._evidence_results_conflict(left, right)
        )
        relevant = set(case["relevant_entry_ids"])
        ranks = [index + 1 for index, entry_id in enumerate(retrieved) if entry_id in relevant]
        cutoffs = sorted({1, 3, 5, top_k})
        per_case.append({
            "id": str(case["id"]),
            "query": str(case["query"]),
            "category": str(case["category"]),
            "review_status": str(case.get("review_status", "draft")),
            "evaluation_split": str(case.get("evaluation_split", "calibration")),
            "batch_id": str(case.get("batch_id", "")),
            "relevant_entry_ids": sorted(relevant),
            "retrieved_entry_ids": retrieved,
            "accepted_retrieved_entry_ids": accepted_retrieved,
            "selected_retrieved_entry_ids": selected_retrieved,
            "selected_relevant_entry_ids": sorted(
                relevant & set(selected_retrieved[:top_k])
            ),
            "evidence_selection_trace": selection["trace"],
            "selected_duplicate_pair_count": selected_duplicate_pairs,
            "selected_conflict_pair_count": selected_conflict_pairs,
            "accepted_relevant_entry_ids": sorted(
                relevant & set(accepted_retrieved[:top_k])
            ),
            "rejected_relevant_entry_ids": sorted(
                (relevant & set(retrieved[:top_k])) - set(accepted_retrieved[:top_k])
            ),
            "first_relevant_rank": min(ranks) if ranks else None,
            "reciprocal_rank": round(1.0 / min(ranks), 4) if ranks else 0.0,
            "hits": {
                cutoff: 1.0 if any(entry_id in relevant for entry_id in retrieved[:cutoff]) else 0.0
                for cutoff in cutoffs
            },
            "recall": {cutoff: round(len(relevant & set(retrieved[:cutoff])) / len(relevant), 4) for cutoff in cutoffs},
        })
    overall = _metrics(per_case, top_k)
    categories = {
        category: _metrics([item for item in per_case if item["category"] == category], top_k)
        for category in sorted({item["category"] for item in per_case})
    }
    splits = {
        split: _metrics(
            [item for item in per_case if item["evaluation_split"] == split],
            top_k,
        )
        for split in sorted(EVALUATION_SPLITS)
    }
    relevant_item_count = sum(len(item["relevant_entry_ids"]) for item in per_case)
    accepted_relevant_item_count = sum(
        len(item["accepted_relevant_entry_ids"]) for item in per_case
    )
    accepted_item_count = sum(
        len(item["accepted_retrieved_entry_ids"][:top_k]) for item in per_case
    )
    evidence_gate = {
        "policy": "deterministic-condition-audit-v1",
        "evaluated_cases": len(per_case),
        "relevant_item_count": relevant_item_count,
        "accepted_relevant_item_count": accepted_relevant_item_count,
        "relevant_preservation_rate": round(
            accepted_relevant_item_count / relevant_item_count,
            4,
        ) if relevant_item_count else 0.0,
        "accepted_precision_at_k": round(
            accepted_relevant_item_count / accepted_item_count,
            4,
        ) if accepted_item_count else 0.0,
        "rejected_relevant_case_ids": [
            item["id"] for item in per_case if item["rejected_relevant_entry_ids"]
        ],
    }
    selected_relevant_item_count = sum(
        len(item["selected_relevant_entry_ids"]) for item in per_case
    )
    evidence_selection = {
        "policy": evidence_selection_policy,
        "evaluated_cases": len(per_case),
        "relevant_item_count": relevant_item_count,
        "selected_relevant_item_count": selected_relevant_item_count,
        "relevant_preservation_rate": round(
            selected_relevant_item_count / relevant_item_count,
            4,
        ) if relevant_item_count else 0.0,
        "selected_duplicate_pair_count": sum(
            item["selected_duplicate_pair_count"] for item in per_case
        ),
        "selected_conflict_pair_count": sum(
            item["selected_conflict_pair_count"] for item in per_case
        ),
        "cases_with_empty_selection": [
            item["id"] for item in per_case if not item["selected_retrieved_entry_ids"]
        ],
        "rejected_low_precision_count": sum(
            item["evidence_selection_trace"]["rejected_low_precision_count"]
            for item in per_case
        ),
        "rejected_duplicate_count": sum(
            item["evidence_selection_trace"]["rejected_duplicate_count"]
            for item in per_case
        ),
        "rejected_conflict_count": sum(
            item["evidence_selection_trace"]["rejected_conflict_count"]
            for item in per_case
        ),
    }
    all_approved = [case for case in cases if case.get("review_status") == "approved"]
    approved_by_split = {
        split: [
            case for case in all_approved
            if str(case.get("evaluation_split", "calibration")) == split
        ]
        for split in EVALUATION_SPLITS
    }
    calibration_ready = (
        len(approved_by_split["calibration"]) >= CALIBRATION_MIN_CASES
        and CATEGORIES.issubset({
            str(case.get("category", "")) for case in approved_by_split["calibration"]
        })
    )
    holdout_ready = (
        len(approved_by_split["holdout"]) >= HOLDOUT_MIN_CASES
        and CATEGORIES.issubset({
            str(case.get("category", "")) for case in approved_by_split["holdout"]
        })
        and len({
            str(case.get("batch_id", "")).strip()
            for case in approved_by_split["holdout"]
            if str(case.get("batch_id", "")).strip()
        }) >= 1
    )
    fixed_set_ready = calibration_ready
    evidence_gate_ready = (
        holdout_ready
        and evaluation_split in {"all", "holdout"}
        and not include_draft
        and evidence_gate["relevant_preservation_rate"] == 1.0
    )
    policy_gate_ready = calibration_ready and holdout_ready and evidence_gate_ready
    w2_evidence_set_ready = (
        policy_gate_ready
        and evidence_selection_policy == "evidence-set-v2"
        and evidence_selection["relevant_preservation_rate"] == 1.0
        and evidence_selection["selected_duplicate_pair_count"] == 0
        and evidence_selection["selected_conflict_pair_count"] == 0
    )
    recall_at_5 = overall["recall"].get("@5", 0.0)
    teacher_phrase_miss = 1.0 - categories.get("teacher_phrase", {}).get("hit_rate", {}).get("@5", 0.0)
    threshold_evaluable = (
        policy_gate_ready
        and not include_draft
        and evaluation_split in {"all", "holdout"}
        and categories.get("teacher_phrase", {}).get("count", 0) > 0
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "retrieval-fixed-set-benchmark",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "top_k": top_k,
        "ranking_policy": ranking_policy,
        "evidence_selection_policy": evidence_selection_policy,
        "evaluation_split": evaluation_split,
        "include_draft": include_draft,
        "fixed_set_ready": fixed_set_ready,
        "calibration_ready": calibration_ready,
        "holdout_ready": holdout_ready,
        "evidence_gate_ready": evidence_gate_ready,
        "policy_gate_ready": policy_gate_ready,
        "w2_evidence_set_ready": w2_evidence_set_ready,
        "threshold_evaluable": threshold_evaluable,
        "upgrade_recommended": bool(threshold_evaluable and (recall_at_5 < 0.85 or teacher_phrase_miss > 0.15)),
        "validation": validation,
        "eligible_cases": len(eligible),
        "excluded_cases": len(cases) - len(eligible),
        "overall": overall,
        "by_category": categories,
        "by_split": splits,
        "evidence_gate": evidence_gate,
        "evidence_selection": evidence_selection,
        "missed_case_ids_at_5": [item["id"] for item in per_case if not item["hits"].get(5, 0.0)],
        "per_case": per_case,
        "notes": [
            "Draft cases are exploratory and cannot trigger retrieval-policy changes.",
            "Backend enhancement requires at least 30 teacher-approved cases.",
            "The approved fixed set must cover knowledge point, problem type, error type, and teacher phrasing.",
            "A policy change additionally requires an independent approved holdout batch with at least 12 cases covering all categories.",
            "Legacy cases without evaluation_split are calibration cases and cannot satisfy the holdout gate.",
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
    print(f"- ranking_policy: {report['ranking_policy']}")
    print(f"- evidence_selection_policy: {report['evidence_selection_policy']}")
    print(f"- evaluation_split: {report['evaluation_split']}")
    print(
        f"- calibration_ready: {report['calibration_ready']}; "
        f"holdout_ready: {report['holdout_ready']}; "
        f"evidence_gate_ready: {report['evidence_gate_ready']}; "
        f"policy_gate_ready: {report['policy_gate_ready']}; "
        f"w2_evidence_set_ready: {report['w2_evidence_set_ready']}"
    )
    print(f"- upgrade_recommended: {report['upgrade_recommended']}")
    print(
        f"- Recall@5: {report['overall']['recall'].get('@5', 0.0)}; MRR: {report['overall']['mrr']}; empty_rate: {report['overall']['empty_rate']}"
    )
    print(
        f"- evidence relevant preservation: "
        f"{report['evidence_gate']['relevant_preservation_rate']}; "
        f"rejected relevant cases: "
        f"{len(report['evidence_gate']['rejected_relevant_case_ids'])}"
    )
    print(
        f"- selected relevant preservation: "
        f"{report['evidence_selection']['relevant_preservation_rate']}; "
        f"selected duplicate/conflict pairs: "
        f"{report['evidence_selection']['selected_duplicate_pair_count']}/"
        f"{report['evidence_selection']['selected_conflict_pair_count']}"
    )
    print()
    print("| category | n | Hit@5 | Recall@5 | MRR | empty |")
    print("|---|---:|---:|---:|---:|---:|")
    for category, metrics in report["by_category"].items():
        print(
            f"| {category} | {metrics['count']} | {metrics['hit_rate'].get('@5', 0.0)} | {metrics['recall'].get('@5', 0.0)} | {metrics['mrr']} | {metrics['empty_rate']} |"
        )
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
    seed.add_argument(
        "--evaluation-split",
        choices=tuple(sorted(EVALUATION_SPLITS)),
        default="calibration",
    )
    seed.add_argument("--batch-id", default="")
    seed.add_argument(
        "--append",
        action="store_true",
        help="append a new split while excluding queries and calibration entries already in the file",
    )
    seed.add_argument("--force", action="store_true", help="replace an existing local case file")
    sub.add_parser("validate", help="validate case schema and entry references")
    run = sub.add_parser("run", help="run the fixed-set retrieval benchmark")
    run.add_argument("--top-k", type=int, default=5)
    run.add_argument("--include-draft", action="store_true")
    run.add_argument(
        "--evaluation-split",
        choices=("all", "calibration", "holdout"),
        default="all",
    )
    run.add_argument(
        "--ranking-policy",
        choices=("baseline", "multi-route", "intent-augmented"),
        default="baseline",
    )
    run.add_argument(
        "--evidence-selection-policy",
        choices=("baseline", "precision-gated-v1", "evidence-set-v2"),
        default="baseline",
    )
    run.add_argument("--format", choices=("json", "markdown"), default="json")
    run.add_argument("--record", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    library = args.library.expanduser().resolve()
    cases_path = args.cases.expanduser().resolve() if args.cases else default_cases_path(library)
    if args.command == "seed":
        if cases_path.exists() and not args.force and not args.append:
            raise FileExistsError(f"case file already exists: {cases_path}; use --force to replace it")
        existing = load_cases(cases_path) if args.append else []
        if args.evaluation_split == "holdout" and not args.batch_id.strip():
            raise ValueError("holdout seed requires a non-empty --batch-id")
        excluded_queries = {
            str(case.get("query", "")).strip()
            for case in existing
            if str(case.get("query", "")).strip()
        }
        excluded_entry_ids = {
            str(entry_id)
            for case in existing
            if str(case.get("evaluation_split", "calibration")) == "calibration"
            for entry_id in case.get("relevant_entry_ids", [])
        } if args.evaluation_split == "holdout" else set()
        cases = seed_cases(
            library,
            limit=max(1, args.limit),
            evaluation_split=args.evaluation_split,
            batch_id=args.batch_id,
            excluded_entry_ids=excluded_entry_ids,
            excluded_queries=excluded_queries,
        )
        write_cases(cases_path, [*existing, *cases])
        print(
            json.dumps(
                {"status": "seeded", "path": str(cases_path), "case_count": len(cases), "review_status": "draft"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    cases = load_cases(cases_path)
    if args.command == "validate":
        result = validate_cases(library, cases)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["valid"] else 2
    report = run_benchmark(
        library,
        cases,
        top_k=max(1, args.top_k),
        include_draft=args.include_draft,
        ranking_policy=args.ranking_policy,
        evaluation_split=args.evaluation_split,
        evidence_selection_policy=args.evidence_selection_policy,
    )
    if args.record:
        if not report["fixed_set_ready"]:
            raise ValueError(
                "--record requires at least 30 teacher-approved cases; draft results are not durable evidence"
            )
        report["archive_event"] = record_report(
            library,
            report,
            {
                "case_count": report["eligible_cases"],
                "top_k": report["top_k"],
                "ranking_policy": report["ranking_policy"],
                "evidence_selection_policy": report["evidence_selection_policy"],
                "evaluation_split": report["evaluation_split"],
            },
        )
    if args.format == "markdown":
        print_markdown(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
