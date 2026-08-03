#!/usr/bin/env python3
"""Read-only preflight for deterministic Knowledge evidence budgets.

This benchmark compares a generous baseline evidence pack with a smaller
candidate pack.  It verifies teacher-labelled facts are retained and measures
serialized-size savings.  It does not call a model and therefore cannot claim
that answer quality is unchanged; passing only authorizes a later paired model
evaluation.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(SKILL_SCRIPTS))

import knowledge_store  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_CASES = Path("evals") / "evidence-budget-cases.jsonl"
REVIEW_STATUSES = {"draft", "approved", "rejected"}


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number}: case must be an object")
        value["_line"] = line_number
        values.append(value)
    return values


def validate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    seen: set[str] = set()
    approved = 0
    for index, case in enumerate(cases, 1):
        line = case.get("_line", index)
        case_id = str(case.get("id", "")).strip()
        status = str(case.get("review_status", "draft")).strip()
        facts = case.get("required_facts")
        if not case_id:
            errors.append(f"line {line}: missing id")
        elif case_id in seen:
            errors.append(f"line {line}: duplicate id {case_id}")
        seen.add(case_id)
        if not str(case.get("entry_id", "")).strip():
            errors.append(f"line {line}: missing entry_id")
        if not str(case.get("query", "")).strip():
            errors.append(f"line {line}: missing query")
        if status not in REVIEW_STATUSES:
            errors.append(f"line {line}: invalid review_status {status or '<empty>'}")
        if status == "approved":
            approved += 1
        if (
            not isinstance(facts, list)
            or not facts
            or not all(isinstance(item, str) and item.strip() for item in facts)
        ):
            errors.append(f"line {line}: required_facts must be a non-empty string list")
    return {
        "valid": not errors,
        "case_count": len(cases),
        "approved_count": approved,
        "errors": errors,
        "warnings": [] if approved >= 20 else [f"paired preflight needs 20 approved cases; current={approved}"],
    }


def _serialized(pack: dict[str, Any]) -> str:
    return json.dumps(pack, ensure_ascii=False, sort_keys=True)


def evaluate_case(
    library: Path,
    case: dict[str, Any],
    *,
    baseline_chars: int,
    candidate_chars: int,
    top_k: int,
) -> dict[str, Any]:
    common: dict[str, Any] = {
        "root": library,
        "entry_id": str(case["entry_id"]),
        "text": str(case["query"]),
        "task_type": str(case.get("task_type", "answer.revise")),
        "top_k": top_k,
    }
    baseline = knowledge_store.build_agent_evidence(**common, char_budget=baseline_chars)
    candidate = knowledge_store.build_agent_evidence(**common, char_budget=candidate_chars)
    baseline_text = _serialized(baseline)
    candidate_text = _serialized(candidate)
    facts = [str(item).strip() for item in case.get("required_facts", []) if str(item).strip()]
    baseline_hits = [fact for fact in facts if fact in baseline_text]
    candidate_hits = [fact for fact in facts if fact in candidate_text]
    baseline_size = len(baseline_text)
    candidate_size = len(candidate_text)
    savings = max(0.0, 1.0 - candidate_size / baseline_size) if baseline_size else 0.0
    return {
        "id": str(case["id"]),
        "baseline_chars": baseline_size,
        "candidate_chars": candidate_size,
        "savings_ratio": round(savings, 4),
        "required_fact_count": len(facts),
        "baseline_fact_hits": len(baseline_hits),
        "candidate_fact_hits": len(candidate_hits),
        "candidate_fact_retention": round(len(candidate_hits) / len(facts), 4) if facts else 0.0,
        "baseline_missing_facts": [fact for fact in facts if fact not in baseline_hits],
        "candidate_missing_facts": [fact for fact in facts if fact not in candidate_hits],
        "candidate_within_budget": candidate_size <= candidate_chars,
        "baseline_status": baseline.get("status"),
        "candidate_status": candidate.get("status"),
    }


def run(
    library: Path,
    cases: list[dict[str, Any]],
    *,
    baseline_chars: int = 20000,
    candidate_chars: int = 8000,
    top_k: int = 4,
    include_draft: bool = False,
) -> dict[str, Any]:
    validation = validate_cases(cases)
    eligible = [
        case
        for case in cases
        if case.get("review_status") == "approved" or include_draft and case.get("review_status") == "draft"
    ]
    results = [
        evaluate_case(
            library,
            case,
            baseline_chars=baseline_chars,
            candidate_chars=candidate_chars,
            top_k=top_k,
        )
        for case in eligible
    ]
    savings = [float(item["savings_ratio"]) for item in results]
    fact_retention = (
        sum(item["candidate_fact_hits"] for item in results) / sum(item["required_fact_count"] for item in results)
        if results and sum(item["required_fact_count"] for item in results)
        else 0.0
    )
    baseline_complete = all(not item["baseline_missing_facts"] for item in results)
    within_budget = all(item["candidate_within_budget"] for item in results)
    median_savings = round(statistics.median(savings), 4) if savings else 0.0
    enough_cases = len(results) >= 20 and not include_draft
    preflight_passed = (
        validation["valid"]
        and enough_cases
        and baseline_complete
        and within_budget
        and fact_retention == 1.0
        and median_savings >= 0.25
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "evidence-budget-preflight",
        "validation": validation,
        "configuration": {
            "baseline_chars": baseline_chars,
            "candidate_chars": candidate_chars,
            "top_k": top_k,
            "include_draft": include_draft,
        },
        "case_count": len(results),
        "median_savings_ratio": median_savings,
        "required_fact_retention": round(fact_retention, 4),
        "baseline_facts_complete": baseline_complete,
        "all_candidates_within_budget": within_budget,
        "preflight_passed": preflight_passed,
        "authorizes": "paired-model-evaluation" if preflight_passed else "no-policy-change",
        "limitations": [
            "No model is called, so this report cannot prove answer quality is unchanged.",
            "Draft cases may be explored but never authorize a policy change.",
            "Only historical Knowledge evidence is evaluated; current canonical content is out of scope.",
        ],
        "cases": results,
    }


def print_markdown(report: dict[str, Any]) -> None:
    print("# Evidence Budget Preflight")
    print()
    print(
        f"- cases: {report['case_count']}; median savings: {report['median_savings_ratio']:.1%}; "
        f"required fact retention: {report['required_fact_retention']:.1%}"
    )
    print(f"- preflight_passed: {str(report['preflight_passed']).lower()}; authorizes: {report['authorizes']}")
    print()
    print("| case | baseline chars | candidate chars | savings | fact retention | within budget |")
    print("|---|---:|---:|---:|---:|---|")
    for item in report["cases"]:
        print(
            f"| {item['id']} | {item['baseline_chars']} | {item['candidate_chars']} | "
            f"{item['savings_ratio']:.1%} | {item['candidate_fact_retention']:.1%} | "
            f"{'yes' if item['candidate_within_budget'] else 'no'} |"
        )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    value.add_argument("--cases", type=Path)
    value.add_argument("--baseline-chars", type=int, default=20000)
    value.add_argument("--candidate-chars", type=int, default=8000)
    value.add_argument("--top-k", type=int, default=4)
    value.add_argument("--include-draft", action="store_true")
    value.add_argument("--format", choices=("json", "markdown"), default="json")
    return value


def main() -> int:
    args = parser().parse_args()
    library = args.library.expanduser().resolve()
    cases_path = args.cases.expanduser().resolve() if args.cases else library / DEFAULT_CASES
    report = run(
        library,
        load_cases(cases_path),
        baseline_chars=max(1000, min(args.baseline_chars, 20000)),
        candidate_chars=max(1000, min(args.candidate_chars, 20000)),
        top_k=max(1, min(args.top_k, 10)),
        include_draft=args.include_draft,
    )
    report["cases_file"] = str(cases_path)
    if args.format == "markdown":
        print_markdown(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["validation"]["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
