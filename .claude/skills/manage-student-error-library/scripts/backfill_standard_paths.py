#!/usr/bin/env python3
"""Apply audited standard-solution-path backfills without contacting a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import difficulty_assessment
import kb


def load_manifest(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest.entries must be a non-empty array")
    return entries


def expand_path_spec(spec: dict) -> dict:
    steps = spec.get("steps")
    if not isinstance(steps, list) or not steps:
        return spec
    graph = []
    for index, raw in enumerate(steps):
        relations = [str(item).strip() for item in raw.get("relations", []) if str(item).strip()]
        if not relations:
            raise ValueError("each path step must contain a decisive relation")
        unit_ids = [str(item).strip() for item in raw.get("knowledge_units", []) if str(item).strip()]
        graph.append({
            "id": str(raw.get("id") or f"R{index + 1}"),
            "operation": str(raw.get("operation", "")).strip(),
            "cognitive_operation": str(raw.get("cognitive_operation", "")).strip(),
            "depends_on": raw.get(
                "depends_on",
                [str(steps[index - 1].get("id") or f"R{index}")] if index else [],
            ),
            "target_ids": raw.get("target_ids", ["T1"]),
            "decisive_relations": relations,
            "knowledge_units": [
                {
                    "id": unit_id,
                    "relation_indexes": list(range(len(relations))),
                }
                for unit_id in unit_ids
            ],
        })
    operations = [item["operation"] for item in graph]
    return {
        "schema_version": 1,
        "source": str(spec.get("source", "teacher-requested-standard-path-backfill.v1")),
        "selected_path": " → ".join(operations),
        "high_school_basis": spec.get("high_school_basis", []),
        "physical_stages": spec.get("physical_stages", []),
        "reasoning_steps": operations,
        "reasoning_graph": graph,
        "decisive_relations": [
            relation for item in graph for relation in item["decisive_relations"]
        ],
        "representation_transforms": spec.get("representation_transforms", []),
        "condition_checks": spec.get("condition_checks", []),
        "type_distance": spec.get("type_distance"),
        "target_count": int(spec.get("target_count", 1)),
        "transition_count": int(
            spec.get("transition_count", max(0, len(spec.get("physical_stages", [])) - 1))
        ),
        "provenance_note": str(spec.get("provenance_note", "")),
    }


def apply_backfills(
    library: Path,
    manifest_path: Path,
    *,
    check_only: bool = False,
) -> dict:
    prepared: list[tuple[str, Path, dict, dict, str]] = []
    seen: set[str] = set()
    for item in load_manifest(manifest_path):
        if not isinstance(item, dict):
            raise ValueError("manifest entry must be an object")
        entry_id = str(item.get("entry_id", "")).strip()
        if not entry_id or entry_id in seen:
            raise ValueError("entry_id must be non-empty and unique")
        seen.add(entry_id)
        entry = library / "entries" / entry_id
        problem_path = entry / "problem.md"
        record_path = entry / "record.json"
        if not entry.is_dir() or not problem_path.is_file() or not record_path.is_file():
            raise ValueError(f"entry is incomplete: {entry_id}")
        problem = problem_path.read_text(encoding="utf-8")
        digest = hashlib.sha256(problem.encode("utf-8")).hexdigest()
        if digest != str(item.get("problem_sha256", "")):
            raise ValueError(f"problem digest changed: {entry_id}")
        raw_path = item.get("standard_solution_path")
        if not isinstance(raw_path, dict):
            raise ValueError(f"standard solution path must be an object: {entry_id}")
        path = expand_path_spec(raw_path)
        normalized = difficulty_assessment._normalize_path(path)
        if normalized is None:
            raise ValueError(f"standard solution path is invalid: {entry_id}")
        record = kb.load_json(record_path, {})
        if record.get("status") != "ready":
            raise ValueError(f"entry is not ready: {entry_id}")
        prepared.append((entry_id, record_path, record, path, problem))

    results = []
    for entry_id, record_path, record, path, problem in prepared:
        previous = record.get("standard_solution_path")
        if isinstance(previous, dict) and previous != path:
            history = record.setdefault("standard_solution_path_history", [])
            if isinstance(history, list):
                history.append({
                    "reason": "teacher-requested-legacy-backfill-replaced",
                    "path": previous,
                })
                record["standard_solution_path_history"] = history[-5:]
        if not check_only:
            record["standard_solution_path"] = path
            record["updated_at"] = difficulty_assessment.now_iso()
            kb.write_json(record_path, record)
        results.append({
            "entry_id": entry_id,
            "status": "validated" if check_only else "backfilled",
            "input_digest": difficulty_assessment.input_digest(problem, path),
        })
    if not check_only:
        kb.rebuild_index(library)
    return {"status": "completed", "count": len(results), "entries": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate all entries and paths without modifying the library",
    )
    args = parser.parse_args()
    result = apply_backfills(
        args.library.resolve(),
        args.manifest.resolve(),
        check_only=args.check,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
