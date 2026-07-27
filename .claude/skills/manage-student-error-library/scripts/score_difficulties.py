#!/usr/bin/env python3
"""Backfill objective-difficulty rubrics for ready entries without contacting a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import difficulty_assessment
import kb


def reviewed_w3_standard_path(entry: Path, problem: str) -> dict | None:
    """Promote only a W3 blueprint tied to the currently reviewed problem bytes."""
    review = kb.load_json(entry / "source-review.json", {})
    problem_sha256 = hashlib.sha256(problem.encode("utf-8")).hexdigest()
    if review.get("status") != "passed" or review.get("problem_sha256") != problem_sha256:
        return None
    shadow = kb.load_json(entry / "w3-shadow-report.json", {})
    report = shadow.get("report") if shadow.get("status") == "completed" else None
    blueprint = report.get("blueprint") if isinstance(report, dict) else None
    standard_path = difficulty_assessment.standard_path_from_blueprint(
        blueprint,
        report,
    )
    if standard_path is None:
        return None
    return {
        **standard_path,
        "source_problem_sha256": problem_sha256,
        "source_completed_at": str(shadow.get("completed_at", "")),
    }


def assess(
    entry: Path,
    *,
    force: bool = False,
    overwrite_teacher_edits: bool = False,
    promote_reviewed_w3_blueprints: bool = False,
) -> dict:
    record = kb.load_json(entry / "record.json", {})
    problem = (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").is_file() else ""
    standard_path = record.get("standard_solution_path")
    promoted = False
    if promote_reviewed_w3_blueprints:
        promoted_path = reviewed_w3_standard_path(entry, problem)
        if promoted_path is not None:
            standard_path = promoted_path
            record["standard_solution_path"] = promoted_path
            promoted = True
    current = record.get("difficulty_assessment")
    if isinstance(current, dict) and current.get("status") == "teacher-edited" and not overwrite_teacher_edits:
        return {"entry_id": entry.name, "status": "teacher-edited", "score": current.get("score")}
    if not isinstance(standard_path, dict):
        if (
            isinstance(current, dict)
            and current.get("rubric_version") == difficulty_assessment.RUBRIC_VERSION
            and current.get("status") == "awaiting-standard-path"
        ):
            return {
                "entry_id": entry.name,
                "status": "awaiting-standard-path",
                "score": None,
                "level": "未评分",
            }
        if isinstance(current, dict) and current.get("score") is not None:
            history = record.setdefault("difficulty_assessment_history", [])
            if isinstance(history, list):
                history.append({
                    "reason": (
                        f"迁移到 {difficulty_assessment.RUBRIC_VERSION}："
                        "缺少规范化标准解题路径，撤下旧版精确分并等待可追溯证据"
                    ),
                    "assessment": current,
                })
                record["difficulty_assessment_history"] = history[-10:]
        record["difficulty_assessment"] = difficulty_assessment.unavailable_assessment(
            problem
        )
        record["updated_at"] = difficulty_assessment.now_iso()
        kb.write_json(entry / "record.json", record)
        return {
            "entry_id": entry.name,
            "status": "awaiting-standard-path",
            "score": None,
            "level": "未评分",
        }
    if not force and difficulty_assessment.current(current, problem, standard_path):
        return {"entry_id": entry.name, "status": "current", "score": current.get("score")}
    assessment = difficulty_assessment.auto_assess(record, problem, standard_path)
    if isinstance(current, dict) and current.get("score") is not None:
        history = record.setdefault("difficulty_assessment_history", [])
        if isinstance(history, list):
            history.append({
                "reason": (
                    f"迁移到 {difficulty_assessment.RUBRIC_VERSION}："
                    "过程维改按状态组合拓扑、运算维改按必要计算链，"
                    "条件维补入关键路径中的决定性审查证据"
                ),
                "assessment": current,
            })
            record["difficulty_assessment_history"] = history[-10:]
    record["difficulty_assessment"] = assessment
    if assessment.get("score") is not None:
        record["difficulty"] = assessment["level"]
    record["updated_at"] = difficulty_assessment.now_iso()
    kb.write_json(entry / "record.json", record)
    return {
        "entry_id": entry.name,
        "status": "promoted-w3-and-scored" if promoted else "scored",
        "score": assessment["score"],
        "level": assessment["level"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--entry-id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--overwrite-teacher-edits", action="store_true", help="Explicitly replace teacher-calibrated assessments")
    parser.add_argument(
        "--promote-reviewed-w3-blueprints",
        action="store_true",
        help="Use completed W3 blueprints only when source-review.json matches current problem bytes",
    )
    args = parser.parse_args()
    root = args.library.resolve()
    entries = [root / "entries" / args.entry_id] if args.entry_id else list(kb.entry_dirs(root))
    results = [
        assess(
            entry,
            force=args.force,
            overwrite_teacher_edits=args.overwrite_teacher_edits,
            promote_reviewed_w3_blueprints=args.promote_reviewed_w3_blueprints,
        )
        for entry in entries
        if entry.is_dir()
        and kb.load_json(entry / "record.json", {}).get("status") == "ready"
    ]
    kb.rebuild_index(root)
    print(json.dumps({"status": "completed", "count": len(results), "entries": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
