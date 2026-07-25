#!/usr/bin/env python3
"""Backfill objective-difficulty rubrics for ready entries without contacting a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import difficulty_assessment
import kb


def assess(entry: Path, *, force: bool = False, overwrite_teacher_edits: bool = False) -> dict:
    record = kb.load_json(entry / "record.json", {})
    problem = (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").is_file() else ""
    answer_path = entry / "student-solution.md"
    answer = answer_path.read_text(encoding="utf-8") if answer_path.is_file() else ""
    model_path = entry / "physics-model.json"
    model = kb.load_json(model_path, {}) if model_path.is_file() else None
    current = record.get("difficulty_assessment")
    if isinstance(current, dict) and current.get("status") == "teacher-edited" and not overwrite_teacher_edits:
        return {"entry_id": entry.name, "status": "teacher-edited", "score": current.get("score")}
    if not force and difficulty_assessment.current(current, problem, answer, model):
        return {"entry_id": entry.name, "status": "current", "score": current.get("score")}
    assessment = difficulty_assessment.auto_assess(record, problem, answer, model)
    record["difficulty_assessment"] = assessment
    record["difficulty"] = assessment["level"]
    record["updated_at"] = difficulty_assessment.now_iso()
    kb.write_json(entry / "record.json", record)
    return {"entry_id": entry.name, "status": "scored", "score": assessment["score"], "level": assessment["level"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--entry-id")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--overwrite-teacher-edits", action="store_true", help="Explicitly replace teacher-calibrated assessments")
    args = parser.parse_args()
    root = args.library.resolve()
    entries = [root / "entries" / args.entry_id] if args.entry_id else list(kb.entry_dirs(root))
    results = [assess(entry, force=args.force, overwrite_teacher_edits=args.overwrite_teacher_edits) for entry in entries if entry.is_dir() and kb.load_json(entry / "record.json", {}).get("status") == "ready"]
    kb.rebuild_index(root)
    print(json.dumps({"status": "completed", "count": len(results), "entries": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
