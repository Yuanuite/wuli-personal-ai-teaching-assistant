#!/usr/bin/env python3
"""Run a solution-blind IPhO theory answer-sheet evaluation through Agent Gateway.

The solver sees only the official English problem text and a literal description
of the figures. Official solutions remain under ``truth/`` and are not copied
into the Agent Gateway candidate workspace. Scoring is a separate later phase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))

import model_registry  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from runtime_environment import resolved_environment  # noqa: E402

LIBRARY = PROJECT_ROOT / "student-error-library"
DEFAULT_EXPERIMENT = LIBRARY / "evals" / "ipho-2021-full-exam-v1"
QUESTION_IDS = ("T1", "T2", "T3")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_question(manifest: dict[str, Any], question_id: str) -> dict[str, Any]:
    for item in manifest.get("questions", []):
        if isinstance(item, dict) and item.get("id") == question_id:
            return item
    raise ValueError(f"question not found in manifest: {question_id}")


def validate_source(experiment: Path) -> dict[str, Any]:
    manifest_path = experiment / "source-manifest.json"
    manifest = load_json(manifest_path)
    errors: list[str] = []
    total_points = 0.0
    total_subparts = 0
    for question_id in QUESTION_IDS:
        item = manifest_question(manifest, question_id)
        problem = experiment / str(item.get("problem_path", ""))
        solution = experiment / str(item.get("solution_path", ""))
        if not problem.is_file():
            errors.append(f"{question_id}: problem PDF missing")
        elif sha256_file(problem) != item.get("problem_sha256"):
            errors.append(f"{question_id}: problem digest mismatch")
        if not solution.is_file():
            errors.append(f"{question_id}: solution PDF missing")
        elif sha256_file(solution) != item.get("solution_sha256"):
            errors.append(f"{question_id}: solution digest mismatch")
        subparts = item.get("subparts", [])
        points = item.get("points", [])
        if not isinstance(subparts, list) or len(subparts) != len(set(subparts)):
            errors.append(f"{question_id}: subpart ids are invalid or duplicated")
        if not isinstance(points, list) or len(points) != len(subparts):
            errors.append(f"{question_id}: point vector does not match subparts")
        question_points = sum(float(point) for point in points)
        if abs(question_points - 10.0) > 1e-9:
            errors.append(f"{question_id}: points sum to {question_points}, expected 10")
        total_points += question_points
        total_subparts += len(subparts)
        text = experiment / "source" / "problems" / f"{question_id}-problem-en.txt"
        figures = experiment / "source" / "figure-facts" / f"{question_id}.md"
        if not text.is_file() or not text.read_text(encoding="utf-8").strip():
            errors.append(f"{question_id}: extracted problem text missing")
        if not figures.is_file() or not figures.read_text(encoding="utf-8").strip():
            errors.append(f"{question_id}: figure facts missing")
    scope = manifest.get("scope", {})
    if scope.get("included") != list(QUESTION_IDS):
        errors.append("scope.included must be exactly T1, T2, T3")
    if int(scope.get("subpart_count", -1)) != total_subparts:
        errors.append("scope.subpart_count mismatch")
    if abs(float(scope.get("maximum_points", -1)) - total_points) > 1e-9:
        errors.append("scope.maximum_points mismatch")
    return {
        "status": "passed" if not errors else "failed",
        "experiment_id": manifest.get("experiment_id"),
        "question_count": len(QUESTION_IDS),
        "subpart_count": total_subparts,
        "maximum_points": total_points,
        "errors": errors,
    }


def answer_sheet_contract(question_id: str, subpart_count: int) -> dict[str, Any]:
    return {
        "name": "wuli.ipho.answer-sheet.v1",
        "instructions": (
            "Return one complete closed-book answer sheet. Every official subpart must appear exactly once "
            "and in official order. Give a self-contained derivation, the final symbolic/numerical answer, "
            "and at least one check where applicable. Do not mention or guess the official solution."
        ),
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status",
                "schema_version",
                "exam_id",
                "question_id",
                "subanswers",
                "unresolved",
            ],
            "properties": {
                "status": {"type": "string", "enum": ["completed"]},
                "schema_version": {"type": "string", "enum": ["wuli.ipho.answer-sheet.v1"]},
                "exam_id": {"type": "string", "enum": ["ipho-2021-theory"]},
                "question_id": {"type": "string", "enum": [question_id]},
                "subanswers": {
                    "type": "array",
                    "minItems": subpart_count,
                    "maxItems": subpart_count,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "subpart_id",
                            "derivation",
                            "final_answer",
                            "checks",
                            "assumptions",
                            "confidence",
                        ],
                        "properties": {
                            "subpart_id": {"type": "string"},
                            "derivation": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 16,
                                "items": {"type": "string"},
                            },
                            "final_answer": {"type": "string"},
                            "checks": {
                                "type": "array",
                                "maxItems": 6,
                                "items": {"type": "string"},
                            },
                            "assumptions": {
                                "type": "array",
                                "maxItems": 6,
                                "items": {"type": "string"},
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                    },
                },
                "unresolved": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
    }


def payload_errors(payload: dict[str, Any], question: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("status") != "completed":
        errors.append("status must be completed")
    if payload.get("schema_version") != "wuli.ipho.answer-sheet.v1":
        errors.append("schema_version mismatch")
    if payload.get("exam_id") != "ipho-2021-theory":
        errors.append("exam_id mismatch")
    if payload.get("question_id") != question.get("id"):
        errors.append("question_id mismatch")
    expected = question.get("subparts", [])
    subanswers = payload.get("subanswers", [])
    if not isinstance(subanswers, list):
        return errors + ["subanswers must be an array"]
    actual = [item.get("subpart_id") if isinstance(item, dict) else None for item in subanswers]
    if actual != expected:
        errors.append(f"subpart order mismatch: expected {expected}, got {actual}")
    for index, item in enumerate(subanswers):
        if not isinstance(item, dict):
            errors.append(f"subanswer {index} is not an object")
            continue
        if not str(item.get("final_answer", "")).strip():
            errors.append(f"{actual[index]}: empty final answer")
        derivation = item.get("derivation", [])
        if not isinstance(derivation, list) or not any(str(line).strip() for line in derivation):
            errors.append(f"{actual[index]}: empty derivation")
    return errors


def aggregate_usage(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, int] = {}
    reported_attempts = 0
    for attempt in attempts:
        usage = attempt.get("token_usage")
        if not isinstance(usage, dict):
            continue
        reported_attempts += 1
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[key] = totals.get(key, 0) + value
    if reported_attempts == 0:
        return {
            "status": "unavailable",
            "reason": "provider-did-not-report-token-usage",
        }
    totals["reported_attempts"] = reported_attempts
    totals["status"] = "reported"
    return totals


def completed_run_record(experiment: Path, question_id: str) -> dict[str, Any]:
    records = []
    for path in sorted((experiment / "results").glob(f"{question_id}-run*.json")):
        try:
            record = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if record.get("status") == "completed":
            records.append(record)
    if len(records) != 1:
        raise ValueError(f"{question_id} needs exactly one completed solve record, found {len(records)}")
    return records[0]


def solve_run_records(experiment: Path, question_id: str) -> list[dict[str, Any]]:
    records = []
    for path in sorted((experiment / "results").glob(f"{question_id}-run*.json")):
        try:
            record = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        records.append(record)
    return records


def token_totals(records: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, int] = {}
    unavailable = 0
    for record in records:
        usage = record.get("token_usage")
        if not isinstance(usage, dict) or usage.get("status") != "reported":
            unavailable += 1
            continue
        for key, value in usage.items():
            if key.endswith("_tokens") and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                totals[key] = totals.get(key, 0) + value
    if not totals:
        return {
            "status": "unavailable",
            "unavailable_records": unavailable,
            "reason": "providers-did-not-report-token-usage",
        }
    totals["total_tokens"] = totals.get(
        "total_tokens",
        totals.get("input_tokens", 0) + totals.get("output_tokens", 0),
    )
    totals["status"] = "reported"
    totals["unavailable_records"] = unavailable
    return totals


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ")


def aggregate_results(experiment: Path) -> dict[str, Any]:
    source_check = validate_source(experiment)
    if source_check["status"] != "passed":
        raise RuntimeError("source validation failed: " + "; ".join(source_check["errors"]))
    freeze = freeze_candidates(experiment)
    unlock = load_json(experiment / "results" / "truth-unlocked.json")
    freeze_path = experiment / "results" / "candidate-freeze.json"
    if unlock.get("candidate_freeze_sha256") != sha256_file(freeze_path):
        raise ValueError("truth unlock no longer matches the candidate freeze")

    manifest = load_json(experiment / "source-manifest.json")
    questions: list[dict[str, Any]] = []
    all_valid_solve_records = []
    all_failed_solve_records = []
    all_grade_records = []
    total_awarded = 0.0
    total_maximum = 0.0
    full_credit_subparts = 0
    needs_review: list[str] = []

    for question_id in QUESTION_IDS:
        question = manifest_question(manifest, question_id)
        solve_record = completed_run_record(experiment, question_id)
        candidate_path = experiment / str(solve_record["candidate_path"])
        if sha256_file(candidate_path) != solve_record.get("candidate_sha256"):
            raise ValueError(f"{question_id}: candidate digest changed before aggregation")
        candidate = load_json(candidate_path)
        grade_record_path = experiment / "results" / f"{question_id}-grade-run.json"
        grade_record = load_json(grade_record_path)
        grade_path = experiment / str(grade_record["grade_path"])
        if sha256_file(grade_path) != grade_record.get("grade_sha256"):
            raise ValueError(f"{question_id}: grade digest changed before aggregation")
        grade = load_json(grade_path)
        grade_errors = grade_payload_errors(grade, question)
        if grade_errors:
            raise ValueError(f"{question_id}: invalid grade: {'; '.join(grade_errors)}")

        run_records = solve_run_records(experiment, question_id)
        failed_records = [record for record in run_records if record.get("status") != "completed"]
        grade_by_id = {item["subpart_id"]: item for item in grade["grades"]}
        subparts = []
        for answer in candidate["subanswers"]:
            item = grade_by_id[answer["subpart_id"]]
            subparts.append({
                "subpart_id": answer["subpart_id"],
                "awarded_points": float(item["awarded_points"]),
                "maximum_points": float(item["max_points"]),
                "verdict": item["verdict"],
                "confidence": item["confidence"],
                "requires_teacher_review": item["requires_teacher_review"],
                "rationale": item["rationale"],
            })
            if item["verdict"] == "full-credit":
                full_credit_subparts += 1
            if item["requires_teacher_review"]:
                needs_review.append(f"{question_id}-{answer['subpart_id']}")

        awarded = sum(item["awarded_points"] for item in subparts)
        maximum = sum(item["maximum_points"] for item in subparts)
        total_awarded += awarded
        total_maximum += maximum
        all_valid_solve_records.append(solve_record)
        all_failed_solve_records.extend(failed_records)
        all_grade_records.append(grade_record)
        questions.append({
            "question_id": question_id,
            "title": question["title"],
            "subpart_count": len(subparts),
            "awarded_points": round(awarded, 4),
            "maximum_points": round(maximum, 4),
            "solve": {
                "wall_seconds": solve_record["wall_seconds"],
                "provider": solve_record.get("provider"),
                "model_id": solve_record.get("model_id"),
                "token_usage": solve_record.get("token_usage"),
                "answer_chars": solve_record.get("answer_chars"),
                "failed_attempt_count": len(failed_records),
                "failed_attempt_wall_seconds": round(
                    sum(float(record.get("wall_seconds", 0)) for record in failed_records),
                    3,
                ),
            },
            "grading": {
                "wall_seconds": grade_record["wall_seconds"],
                "provider": grade_record.get("provider"),
                "model_id": grade_record.get("model_id"),
                "token_usage": grade_record.get("token_usage"),
            },
            "subparts": subparts,
        })

    valid_solve_seconds = round(sum(float(record["wall_seconds"]) for record in all_valid_solve_records), 3)
    failed_solve_seconds = round(sum(float(record.get("wall_seconds", 0)) for record in all_failed_solve_records), 3)
    grading_seconds = round(sum(float(record["wall_seconds"]) for record in all_grade_records), 3)
    summary = {
        "schema_version": 1,
        "experiment_id": manifest["experiment_id"],
        "status": "passed" if not needs_review and abs(total_awarded - total_maximum) < 1e-9 else "review-required",
        "scope": manifest["scope"],
        "source_authority": manifest["source_authority"],
        "isolation": {
            "candidate_count": freeze["candidate_count"],
            "truth_disclosed_during_solve": freeze["truth_disclosed_during_solve"],
            "truth_unlocked_after_freeze": True,
            "candidate_freeze_sha256": sha256_file(freeze_path),
        },
        "score": {
            "awarded_points": round(total_awarded, 4),
            "maximum_points": round(total_maximum, 4),
            "full_credit_subparts": full_credit_subparts,
            "subpart_count": source_check["subpart_count"],
            "needs_teacher_review": needs_review,
        },
        "usage": {
            "valid_solve_agent_seconds_sum": valid_solve_seconds,
            "failed_harness_agent_seconds_sum": failed_solve_seconds,
            "grading_agent_seconds_sum": grading_seconds,
            "all_recorded_agent_seconds_sum": round(valid_solve_seconds + failed_solve_seconds + grading_seconds, 3),
            "solve_tokens": token_totals(all_valid_solve_records),
            "failed_harness_tokens": token_totals(all_failed_solve_records),
            "grading_tokens": token_totals(all_grade_records),
            "note": (
                "Agent-second sums are additive per-question durations. Because questions ran in parallel, "
                "they are not batch elapsed wall-clock time."
            ),
        },
        "questions": questions,
    }
    results = experiment / "results"
    write_json(results / "summary.json", summary)
    _write_answer_sheet(experiment, manifest, questions)
    _write_score_report(experiment, summary)
    return summary


def _write_answer_sheet(
    experiment: Path,
    manifest: dict[str, Any],
    question_summaries: list[dict[str, Any]],
) -> None:
    summary_by_id = {item["question_id"]: item for item in question_summaries}
    lines = [
        "# 2021 IPhO Theory — Closed-Book Answer Sheet",
        "",
        "Official English problems T1–T3; experimental problems E1–E2 are outside scope.",
        "The following answers were frozen before the official solutions were unlocked.",
        "",
    ]
    for question_id in QUESTION_IDS:
        question = manifest_question(manifest, question_id)
        result = summary_by_id[question_id]
        candidate = load_json(experiment / "work" / question_id / "candidate-answer.json")
        lines.extend([
            f"## {question_id} — {question['title']} ({result['awarded_points']:g}/{result['maximum_points']:g})",
            "",
        ])
        for answer in candidate["subanswers"]:
            lines.extend([f"### {answer['subpart_id']}", ""])
            for index, step in enumerate(answer["derivation"], start=1):
                lines.append(f"{index}. {step}")
            lines.extend(["", f"**Final answer:** {answer['final_answer']}", ""])
            if answer["checks"]:
                lines.append("Checks:")
                lines.append("")
                lines.extend(f"- {check}" for check in answer["checks"])
                lines.append("")
            if answer["assumptions"]:
                lines.append("Assumptions:")
                lines.append("")
                lines.extend(f"- {item}" for item in answer["assumptions"])
                lines.append("")
            lines.extend([f"Candidate confidence: `{answer['confidence']}`.", ""])
    (experiment / "results" / "ipho-2021-theory-answer-sheet.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )


def _write_score_report(experiment: Path, summary: dict[str, Any]) -> None:
    score = summary["score"]
    usage = summary["usage"]
    grade_tokens = usage["grading_tokens"]
    lines = [
        "# 2021 IPhO Theory — Closed-Book Score Report",
        "",
        f"- Result: **{score['awarded_points']:g}/{score['maximum_points']:g}**",
        f"- Atomic subparts: **{score['full_credit_subparts']}/{score['subpart_count']} full credit**",
        f"- Teacher-review items: **{len(score['needs_teacher_review'])}**",
        "- Isolation: all three candidates were frozen before official solutions were unlocked; "
        f"`truth_disclosed_during_solve={str(summary['isolation']['truth_disclosed_during_solve']).lower()}`.",
        "",
        "## Per-question time and usage",
        "",
        "| Question | Score | Valid solve | Solve token usage | Answer chars | Failed harness overhead | Independent grading | Grading tokens |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for question in summary["questions"]:
        solve = question["solve"]
        grading = question["grading"]
        solve_usage = solve["token_usage"]
        solve_tokens = (
            str(solve_usage.get("total_tokens")) if solve_usage.get("status") == "reported" else "unavailable"
        )
        grading_usage = grading["token_usage"]
        grading_total = grading_usage.get(
            "total_tokens",
            grading_usage.get("input_tokens", 0) + grading_usage.get("output_tokens", 0),
        )
        lines.append(
            "| {qid} | {score:g}/{maximum:g} | {solve_time:.3f}s | {solve_tokens} | "
            "{chars} | {failed:.3f}s | {grade_time:.3f}s | {grade_tokens} |".format(
                qid=question["question_id"],
                score=question["awarded_points"],
                maximum=question["maximum_points"],
                solve_time=solve["wall_seconds"],
                solve_tokens=solve_tokens,
                chars=solve["answer_chars"],
                failed=solve["failed_attempt_wall_seconds"],
                grade_time=grading["wall_seconds"],
                grade_tokens=grading_total,
            )
        )
    lines.extend([
        "",
        "The solver provider did not expose token counters, so solve tokens are intentionally reported as "
        "`unavailable`; answer characters are a transport-size observation, not a token estimate.",
        "",
        "Additive recorded consumption:",
        "",
        f"- valid solve agent time: {usage['valid_solve_agent_seconds_sum']:.3f}s;",
        f"- failed harness agent time: {usage['failed_harness_agent_seconds_sum']:.3f}s;",
        f"- independent grading agent time: {usage['grading_agent_seconds_sum']:.3f}s;",
        f"- all recorded agent time: {usage['all_recorded_agent_seconds_sum']:.3f}s;",
        f"- grading tokens: {grade_tokens.get('input_tokens', 0)} input + "
        f"{grade_tokens.get('output_tokens', 0)} output = {grade_tokens.get('total_tokens', 0)} total.",
        "",
        "These times are sums of per-question runs, not batch wall-clock time; the three questions were run "
        "in parallel.",
        "",
        "## Atomic grading",
        "",
        "| Question | Subpart | Score | Verdict | Grader confidence | Rationale |",
        "|---|---|---:|---|---|---|",
    ])
    for question in summary["questions"]:
        for item in question["subparts"]:
            lines.append(
                "| {question} | {subpart} | {score:g}/{maximum:g} | {verdict} | {confidence} | {rationale} |".format(
                    question=question["question_id"],
                    subpart=item["subpart_id"],
                    score=item["awarded_points"],
                    maximum=item["maximum_points"],
                    verdict=item["verdict"],
                    confidence=item["confidence"],
                    rationale=_markdown_cell(item["rationale"]),
                )
            )
    (experiment / "results" / "ipho-2021-theory-score-report.md").write_text(
        "\n".join(lines).rstrip() + "\n", encoding="utf-8"
    )


def freeze_candidates(experiment: Path) -> dict[str, Any]:
    output = experiment / "results" / "candidate-freeze.json"
    if output.exists():
        existing = load_json(output)
        for item in existing.get("candidates", []):
            path = experiment / str(item.get("candidate_path", ""))
            if not path.is_file() or sha256_file(path) != item.get("candidate_sha256"):
                raise ValueError("existing candidate freeze no longer matches candidate bytes")
        return existing
    candidates = []
    for question_id in QUESTION_IDS:
        record = completed_run_record(experiment, question_id)
        path = experiment / str(record["candidate_path"])
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = sha256_file(path)
        if digest != record.get("candidate_sha256"):
            raise ValueError(f"{question_id}: candidate digest changed after solve")
        candidates.append({
            "question_id": question_id,
            "candidate_path": record["candidate_path"],
            "candidate_sha256": digest,
            "solve_started_at": record["started_at"],
            "solve_completed_at": record["completed_at"],
            "solve_wall_seconds": record["wall_seconds"],
            "token_usage": record["token_usage"],
            "truth_disclosed": record["truth_disclosed"],
        })
    freeze = {
        "schema_version": 1,
        "experiment_id": load_json(experiment / "source-manifest.json")["experiment_id"],
        "status": "frozen",
        "frozen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "question_count": len(candidates),
        "candidate_count": len(candidates),
        "truth_disclosed_during_solve": any(item.get("truth_disclosed") is not False for item in candidates),
        "candidates": candidates,
    }
    if freeze["truth_disclosed_during_solve"]:
        raise PermissionError("cannot freeze: at least one solve disclosed truth")
    write_json(output, freeze)
    return freeze


def unlock_truth(experiment: Path) -> dict[str, Any]:
    output = experiment / "results" / "truth-unlocked.json"
    freeze = freeze_candidates(experiment)
    if output.exists():
        return load_json(output)
    manifest = load_json(experiment / "source-manifest.json")
    solutions = []
    for question_id in QUESTION_IDS:
        question = manifest_question(manifest, question_id)
        solution = experiment / str(question["solution_path"])
        digest = sha256_file(solution)
        if digest != question["solution_sha256"]:
            raise ValueError(f"{question_id}: solution digest changed before unlock")
        solutions.append({
            "question_id": question_id,
            "solution_path": question["solution_path"],
            "solution_sha256": digest,
        })
    record = {
        "schema_version": 1,
        "experiment_id": manifest["experiment_id"],
        "status": "unlocked-for-scoring",
        "unlocked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidate_freeze_sha256": sha256_file(experiment / "results" / "candidate-freeze.json"),
        "candidate_count": freeze["candidate_count"],
        "solutions": solutions,
    }
    write_json(output, record)
    return record


def prepare_entry(experiment: Path, question_id: str) -> Path:
    entry = experiment / "work" / question_id
    entry.mkdir(parents=True, exist_ok=True)
    problem_source = experiment / "source" / "problems" / f"{question_id}-problem-en.txt"
    figure_source = experiment / "source" / "figure-facts" / f"{question_id}.md"
    shutil.copyfile(problem_source, entry / "problem.txt")
    shutil.copyfile(figure_source, entry / "figure-facts.md")
    return entry


def grading_contract(question_id: str, subpart_count: int) -> dict[str, Any]:
    return {
        "name": "wuli.ipho.answer-sheet-grade.v1",
        "instructions": (
            "Grade every subpart against the official solution and its point allocation. Award partial credit "
            "only for a physically correct, relevant step that earns credit under the official derivation. "
            "A final formula with an unjustified or contradictory derivation is not automatically full credit. "
            "Use needs-review only when the extracted official solution is genuinely ambiguous."
        ),
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "schema_version", "exam_id", "question_id", "grades"],
            "properties": {
                "status": {"type": "string", "enum": ["completed"]},
                "schema_version": {
                    "type": "string",
                    "enum": ["wuli.ipho.answer-sheet-grade.v1"],
                },
                "exam_id": {"type": "string", "enum": ["ipho-2021-theory"]},
                "question_id": {"type": "string", "enum": [question_id]},
                "grades": {
                    "type": "array",
                    "minItems": subpart_count,
                    "maxItems": subpart_count,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "subpart_id",
                            "awarded_points",
                            "max_points",
                            "verdict",
                            "candidate_summary",
                            "reference_summary",
                            "rationale",
                            "error_types",
                            "confidence",
                            "requires_teacher_review",
                        ],
                        "properties": {
                            "subpart_id": {"type": "string"},
                            "awarded_points": {"type": "number", "minimum": 0},
                            "max_points": {"type": "number", "minimum": 0},
                            "verdict": {
                                "type": "string",
                                "enum": ["full-credit", "partial-credit", "zero", "needs-review"],
                            },
                            "candidate_summary": {"type": "string"},
                            "reference_summary": {"type": "string"},
                            "rationale": {"type": "string"},
                            "error_types": {
                                "type": "array",
                                "maxItems": 6,
                                "items": {"type": "string"},
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                            "requires_teacher_review": {"type": "boolean"},
                        },
                    },
                },
            },
        },
    }


def grade_payload_errors(payload: dict[str, Any], question: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("status") != "completed":
        errors.append("status must be completed")
    if payload.get("schema_version") != "wuli.ipho.answer-sheet-grade.v1":
        errors.append("schema_version mismatch")
    if payload.get("exam_id") != "ipho-2021-theory":
        errors.append("exam_id mismatch")
    if payload.get("question_id") != question.get("id"):
        errors.append("question_id mismatch")
    expected_ids = list(question.get("subparts", []))
    expected_points = [float(value) for value in question.get("points", [])]
    grades = payload.get("grades", [])
    if not isinstance(grades, list):
        return errors + ["grades must be an array"]
    actual_ids = [item.get("subpart_id") if isinstance(item, dict) else None for item in grades]
    if actual_ids != expected_ids:
        errors.append(f"grade order mismatch: expected {expected_ids}, got {actual_ids}")
    for index, grade in enumerate(grades):
        if not isinstance(grade, dict) or index >= len(expected_points):
            errors.append(f"grade {index} is invalid")
            continue
        maximum = expected_points[index]
        try:
            declared = float(grade.get("max_points"))
            awarded = float(grade.get("awarded_points"))
        except (TypeError, ValueError):
            errors.append(f"{actual_ids[index]}: points are not numeric")
            continue
        if abs(declared - maximum) > 1e-9:
            errors.append(f"{actual_ids[index]}: max_points {declared} does not match official {maximum}")
        if awarded < -1e-9 or awarded > maximum + 1e-9:
            errors.append(f"{actual_ids[index]}: awarded points outside [0, {maximum}]")
        verdict = grade.get("verdict")
        if verdict == "full-credit" and abs(awarded - maximum) > 1e-9:
            errors.append(f"{actual_ids[index]}: full-credit must award all points")
        if verdict == "zero" and abs(awarded) > 1e-9:
            errors.append(f"{actual_ids[index]}: zero verdict must award zero points")
        if verdict == "partial-credit" and not (0 < awarded < maximum):
            errors.append(f"{actual_ids[index]}: partial-credit must be strictly partial")
        if verdict == "needs-review" and grade.get("requires_teacher_review") is not True:
            errors.append(f"{actual_ids[index]}: needs-review must request teacher review")
    return errors


def grade_question(
    experiment: Path,
    question_id: str,
    *,
    model_id: str,
    routing_tier: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    unlock_path = experiment / "results" / "truth-unlocked.json"
    if not unlock_path.is_file():
        raise RuntimeError("truth must be explicitly unlocked after candidate freeze")
    freeze = freeze_candidates(experiment)
    frozen = next(item for item in freeze["candidates"] if item["question_id"] == question_id)
    candidate_source = experiment / frozen["candidate_path"]
    if sha256_file(candidate_source) != frozen["candidate_sha256"]:
        raise ValueError(f"{question_id}: frozen candidate changed before grading")
    manifest = load_json(experiment / "source-manifest.json")
    question = manifest_question(manifest, question_id)
    solution_source = experiment / "truth" / "solutions" / f"{question_id}-solution-en.txt"
    if not solution_source.is_file():
        raise FileNotFoundError(solution_source)
    grading_entry = experiment / "grading" / question_id
    grading_entry.mkdir(parents=True, exist_ok=True)
    grade_path = grading_entry / "grade.json"
    if grade_path.exists():
        raise FileExistsError(f"{question_id} already has a frozen grade")
    shutil.copyfile(candidate_source, grading_entry / "candidate-answer.json")
    shutil.copyfile(solution_source, grading_entry / "official-solution.txt")
    shutil.copyfile(
        experiment / "source" / "problems" / f"{question_id}-problem-en.txt",
        grading_entry / "problem.txt",
    )

    model_registry.LIBRARY = LIBRARY
    resolved_model = model_registry.resolve_model_id_for_task("analysis.generate", routing_tier, model_id)
    model_config = model_registry.model_config_for_task("analysis.generate", resolved_model, routing_tier)
    allocation = ", ".join(f"{subpart}={point:g}" for subpart, point in zip(question["subparts"], question["points"]))
    task = {
        "schema_version": 1,
        "id": f"ipho-2021-grade-{question_id.lower()}-{int(time.time())}",
        "kind": "analysis.generate",
        "entry_id": f"grade-{question_id}",
        "entry_dir": str(grading_entry.resolve()),
        "working_dir": str(grading_entry.resolve()),
        "prompt": (
            f"Independently grade the frozen closed-book answer for 2021 IPhO {question_id}. "
            "Use problem.txt, candidate-answer.json, and official-solution.txt. Do not repair or rewrite "
            "the candidate. Apply the official maximum points exactly and judge the physical derivation, "
            f"not verbal similarity. Official allocation: {allocation}. "
            "For each subpart cite the decisive agreement or discrepancy in concise mathematical terms."
        ),
        "allowed_paths": ["grade.json"],
        "input_paths": ["problem.txt", "candidate-answer.json", "official-solution.txt"],
        "denied_paths": [],
        "hidden_paths": [],
        "requires_change": True,
        "timeout_seconds": timeout_seconds,
        # This grading corpus contains only publicly released official IPhO
        # materials and the generated answer sheet. Project remote-agent policy
        # and the selected model's connection probe still apply in the Gateway.
        "allow_remote": True,
        "routing_tier": routing_tier,
        "model_config": model_config or {},
        "workspace_root": str(experiment / "workspaces"),
        "context_files": {},
        "output_contract": grading_contract(question_id, len(question["subparts"])),
        "structured_context_paths": [
            "problem.txt",
            "candidate-answer.json",
            "official-solution.txt",
        ],
    }

    def materializer(staging: Path, payload: dict[str, Any]) -> dict[str, Any]:
        errors = grade_payload_errors(payload, question)
        if errors:
            raise ValueError("; ".join(errors))
        write_json(staging / "grade.json", payload)
        return {
            "grade_schema": "wuli.ipho.answer-sheet-grade.v1",
            "subpart_count": len(payload["grades"]),
            "candidate_sha256": frozen["candidate_sha256"],
            "official_solution_sha256": question["solution_sha256"],
        }

    def validator(staging: Path, changed: list[str]) -> list[str]:
        errors = [] if changed == ["grade.json"] else [f"unexpected changed files: {changed}"]
        try:
            payload = load_json(staging / "grade.json")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return errors + [f"grade unreadable: {exc}"]
        return errors + grade_payload_errors(payload, question)

    gateway = AgentGateway(environment_resolver=lambda: resolved_environment(LIBRARY))
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    wall_start = time.monotonic()
    gateway_result = gateway.run(task, validator, materializer=materializer)
    wall_seconds = round(time.monotonic() - wall_start, 3)
    completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    attempts = gateway_result.get("attempts", [])
    grade = load_json(grade_path) if grade_path.is_file() else {}
    awarded = round(sum(float(item["awarded_points"]) for item in grade.get("grades", [])), 4)
    record = {
        "schema_version": 1,
        "experiment_id": manifest["experiment_id"],
        "question_id": question_id,
        "status": gateway_result.get("status"),
        "started_at": started_at,
        "completed_at": completed_at,
        "wall_seconds": wall_seconds,
        "provider": gateway_result.get("provider"),
        "model_id": gateway_result.get("model_id", resolved_model),
        "routing_tier": gateway_result.get("routing_tier"),
        "attempt_count": len(attempts),
        "attempts": [
            {
                key: attempt.get(key)
                for key in (
                    "provider",
                    "status",
                    "started_at",
                    "duration_seconds",
                    "failure_type",
                    "token_usage",
                    "validation_errors",
                    "budget_guard",
                )
                if attempt.get(key) is not None
            }
            for attempt in attempts
        ],
        "token_usage": aggregate_usage(attempts),
        "candidate_sha256": frozen["candidate_sha256"],
        "official_solution_sha256": question["solution_sha256"],
        "grade_path": f"grading/{question_id}/grade.json",
        "grade_sha256": sha256_file(grade_path) if grade_path.is_file() else "",
        "awarded_points": awarded,
        "maximum_points": 10.0,
        "needs_teacher_review": [
            item["subpart_id"] for item in grade.get("grades", []) if item.get("requires_teacher_review") is True
        ],
        "validation_errors": gateway_result.get("validation_errors", []),
        "unauthorized_changes": gateway_result.get("unauthorized_changes", []),
        "failure_type": gateway_result.get("failure_type"),
    }
    write_json(experiment / "results" / f"{question_id}-grade-run.json", record)
    return record


def solve_question(
    experiment: Path,
    question_id: str,
    *,
    model_id: str,
    routing_tier: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    if (experiment / "results" / "truth-unlocked.json").exists():
        raise RuntimeError("truth is already unlocked; closed-book solving is no longer allowed")
    source_check = validate_source(experiment)
    if source_check["status"] != "passed":
        raise RuntimeError("source validation failed: " + "; ".join(source_check["errors"]))
    manifest = load_json(experiment / "source-manifest.json")
    question = manifest_question(manifest, question_id)
    entry = prepare_entry(experiment, question_id)
    candidate_path = entry / "candidate-answer.json"
    if candidate_path.exists():
        raise FileExistsError(f"{question_id} already has a frozen candidate")
    existing_records = sorted((experiment / "results").glob(f"{question_id}-run*.json"))
    attempt_no = len(existing_records) + 1
    result_path = experiment / "results" / f"{question_id}-run-{attempt_no:03d}.json"

    model_registry.LIBRARY = LIBRARY
    resolved_model = model_registry.resolve_model_id_for_task("analysis.generate", routing_tier, model_id)
    model_config = model_registry.model_config_for_task("analysis.generate", resolved_model, routing_tier)
    subparts = list(question["subparts"])
    prompt = (
        f"You are sitting the official 2021 IPhO theory exam, question {question_id}: "
        f"{question['title']}. Solve all {len(subparts)} subparts independently and completely.\n"
        "This is a closed-book answer-sheet evaluation. Use only problem.txt and figure-facts.md supplied "
        "below; no official solution, retrieval evidence, web search, file tools, or prior candidate is "
        "available. Mathematical tools explicitly supplied by the problem are allowed. Standard IPhO-level "
        "physics is allowed; the everyday five-step teaching-answer limit does not apply.\n"
        f"Required official order: {', '.join(subparts)}. Preserve signs, domains, limiting cases, units, "
        "and all numerical estimates requested by the paper. If a subpart cannot be completed, still include "
        "it in order, state the furthest justified result, use confidence=low, and list it in unresolved."
    )
    task = {
        "schema_version": 1,
        "id": f"ipho-2021-{question_id.lower()}-{int(time.time())}",
        "kind": "analysis.generate",
        "entry_id": question_id,
        "entry_dir": str(entry.resolve()),
        "working_dir": str(entry.resolve()),
        "prompt": prompt,
        "allowed_paths": ["candidate-answer.json"],
        "input_paths": ["problem.txt", "figure-facts.md"],
        "denied_paths": ["truth/**", "results/**"],
        "hidden_paths": [],
        "requires_change": True,
        "timeout_seconds": timeout_seconds,
        "allow_remote": False,
        "routing_tier": routing_tier,
        "model_config": model_config or {},
        "workspace_root": str(experiment / "workspaces"),
        "context_files": {},
        "output_contract": answer_sheet_contract(question_id, len(subparts)),
        "structured_context_paths": ["problem.txt", "figure-facts.md"],
    }

    def materializer(staging: Path, payload: dict[str, Any]) -> dict[str, Any]:
        errors = payload_errors(payload, question)
        if errors:
            raise ValueError("; ".join(errors))
        # The staging view must contain exactly the two disclosed source files
        # plus the generated output schema. No truth file is ever copied.
        disclosed = sorted(
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file() and path.name != "candidate-answer.json"
        )
        if any(path.startswith(("truth/", "results/")) or "solution" in path.lower() for path in disclosed):
            raise PermissionError(f"solution-bearing file disclosed to solver: {disclosed}")
        write_json(staging / "candidate-answer.json", payload)
        return {
            "answer_schema": "wuli.ipho.answer-sheet.v1",
            "subpart_count": len(payload["subanswers"]),
            "disclosed_files": disclosed,
            "truth_disclosed": False,
        }

    def validator(staging: Path, changed: list[str]) -> list[str]:
        errors: list[str] = []
        if changed != ["candidate-answer.json"]:
            errors.append(f"unexpected changed files: {changed}")
        try:
            candidate = load_json(staging / "candidate-answer.json")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return errors + [f"candidate unreadable: {exc}"]
        return errors + payload_errors(candidate, question)

    gateway = AgentGateway(environment_resolver=lambda: resolved_environment(LIBRARY))
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    wall_start = time.monotonic()
    gateway_result = gateway.run(task, validator, materializer=materializer)
    wall_seconds = round(time.monotonic() - wall_start, 3)
    completed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    attempts = gateway_result.get("attempts", [])
    candidate_sha = sha256_file(candidate_path) if candidate_path.is_file() else ""
    answer_chars = len(candidate_path.read_text(encoding="utf-8")) if candidate_path.is_file() else 0
    run_record = {
        "schema_version": 1,
        "experiment_id": manifest["experiment_id"],
        "question_id": question_id,
        "attempt_no": attempt_no,
        "status": gateway_result.get("status"),
        "started_at": started_at,
        "completed_at": completed_at,
        "wall_seconds": wall_seconds,
        "provider": gateway_result.get("provider"),
        "model_id": gateway_result.get("model_id", resolved_model),
        "model_display_name": gateway_result.get("model_display_name"),
        "routing_tier": gateway_result.get("routing_tier"),
        "attempt_count": len(attempts),
        "attempts": [
            {
                key: attempt.get(key)
                for key in (
                    "provider",
                    "status",
                    "started_at",
                    "duration_seconds",
                    "timeout_seconds",
                    "failure_type",
                    "token_usage",
                    "validation_errors",
                    "budget_guard",
                )
                if attempt.get(key) is not None
            }
            for attempt in attempts
        ],
        "token_usage": aggregate_usage(attempts),
        "problem_chars": len((entry / "problem.txt").read_text(encoding="utf-8")),
        "figure_facts_chars": len((entry / "figure-facts.md").read_text(encoding="utf-8")),
        "answer_chars": answer_chars,
        "candidate_path": f"work/{question_id}/candidate-answer.json",
        "candidate_sha256": candidate_sha,
        "problem_sha256": question["problem_sha256"],
        "solution_sha256_locked": question["solution_sha256"],
        "truth_disclosed": False,
        "materialization": gateway_result.get("materialization", {}),
        "validation_errors": gateway_result.get("validation_errors", []),
        "unauthorized_changes": gateway_result.get("unauthorized_changes", []),
        "failure_type": gateway_result.get("failure_type"),
        "budget_guard": gateway_result.get("budget_guard"),
    }
    write_json(result_path, run_record)
    return run_record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, default=DEFAULT_EXPERIMENT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-source")
    subparsers.add_parser("freeze")
    subparsers.add_parser("unlock-truth")
    subparsers.add_parser("aggregate")
    solve = subparsers.add_parser("solve")
    solve.add_argument("question_id", choices=QUESTION_IDS)
    solve.add_argument("--model-id", default="codex-visualization")
    solve.add_argument("--routing-tier", choices=("auto", "economy", "expert"), default="expert")
    solve.add_argument("--timeout-seconds", type=int, default=1800)
    grade = subparsers.add_parser("grade")
    grade.add_argument("question_id", choices=QUESTION_IDS)
    grade.add_argument("--model-id", default="codex-visualization")
    grade.add_argument("--routing-tier", choices=("auto", "economy", "expert"), default="expert")
    grade.add_argument("--timeout-seconds", type=int, default=1800)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    experiment = args.experiment.resolve()
    if args.command == "validate-source":
        result = validate_source(experiment)
    elif args.command == "freeze":
        result = freeze_candidates(experiment)
    elif args.command == "unlock-truth":
        result = unlock_truth(experiment)
    elif args.command == "aggregate":
        result = aggregate_results(experiment)
    elif args.command == "solve":
        result = solve_question(
            experiment,
            args.question_id,
            model_id=args.model_id,
            routing_tier=args.routing_tier,
            timeout_seconds=max(30, min(args.timeout_seconds, 1800)),
        )
    else:
        result = grade_question(
            experiment,
            args.question_id,
            model_id=args.model_id,
            routing_tier=args.routing_tier,
            timeout_seconds=max(30, min(args.timeout_seconds, 1800)),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return (
        0
        if result.get("status")
        in {
            "passed",
            "completed",
            "frozen",
            "unlocked-for-scoring",
        }
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
