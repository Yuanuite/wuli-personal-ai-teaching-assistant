#!/usr/bin/env python3
"""Deterministic, zero-token fixes for common Agent output failures.

These repairs run on the staging area before candidate promotion.  They are
cheap, auditable, and never call a provider.  Only structural/protocol errors
are repaired; content-quality issues are left to prompt-based repair.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROTECTED_RECORD_FIELDS = {
    "schema_version",
    "id",
    "kind",
    "status",
    "answer_status",
    "created_at",
    "library_folder",
    "source",
    "ocr",
    "source_review",
    "answer_review",
    "visualization_review",
    "generated_from",
    "review",
}


def _restore_protected_fields(staging: Path, entry: Path, errors: list[str]) -> str | None:
    """If the agent changed protected record.json fields, restore from canonical entry."""
    record_path = staging / "record.json"
    if not record_path.exists():
        return None
    changed_fields: set[str] = set()
    for error in errors:
        match = re.search(r"protected field changed:\s*(\S+)", error)
        if match:
            changed_fields.add(match.group(1))
    if not changed_fields:
        return None
    try:
        modified = json.loads(record_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    # Read canonical values from the actual entry directory (source of truth).
    canonical_path = entry / "record.json"
    canonical_data: dict = {}
    if canonical_path.is_file():
        try:
            canonical_data = json.loads(canonical_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    restored_count = 0
    for field in changed_fields & PROTECTED_RECORD_FIELDS:
        if field in canonical_data:
            modified[field] = canonical_data[field]
            restored_count += 1
    if restored_count:
        record_path.write_text(json.dumps(modified, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fields_str = ", ".join(sorted(changed_fields & PROTECTED_RECORD_FIELDS))
        return f"record.json: restored protected fields ({fields_str})"
    return None


def _sync_solution_files(staging: Path, errors: list[str]) -> str | None:
    """If solution.md diverged from teacher-solution.md, sync it."""
    marker = "solution.md must be identical to teacher-solution.md"
    if not any(marker in e for e in errors):
        return None
    teacher = staging / "teacher-solution.md"
    solution = staging / "solution.md"
    if not teacher.is_file():
        return None
    try:
        teacher_text = teacher.read_text(encoding="utf-8")
    except OSError:
        return None
    solution.write_text(teacher_text, encoding="utf-8")
    return "solution.md: synced from teacher-solution.md"


def _ensure_output_files(staging: Path, errors: list[str], attempt: dict) -> list[str]:
    """Ensure required output files exist.  Cannot create content, but can
    note which files are missing for the prompt-based repair tier."""
    # This is a detection-only repair — we can't create content without a
    # model.  Return the list so the caller can decide whether to escalate.
    missing: list[str] = []
    for error in errors:
        match = re.search(r"missing (?:required )?(?:output |entry )?file:\s*(\S+)", error, re.IGNORECASE)
        if match:
            name = match.group(1).strip().strip("\"'")
            if not (staging / name).exists():
                missing.append(name)
    return missing


def apply_script_repairs(staging: Path, entry: Path, validation_errors: list[str], attempt: dict) -> list[str]:
    """Run all deterministic fixes that can be applied without a model.

    Returns a list of human-readable descriptions of what was fixed.
    An empty list means no script-level repair was possible.
    """
    fixes: list[str] = []

    # 1. solution.md ↔ teacher-solution.md sync
    result = _sync_solution_files(staging, validation_errors)
    if result:
        fixes.append(result)

    # 2. Protected record.json field restoration (reads canonical from entry)
    result = _restore_protected_fields(staging, entry, validation_errors)
    if result:
        fixes.append(result)

    # 3. Detect missing output files (informational — no content creation)
    missing = _ensure_output_files(staging, validation_errors, attempt)
    if missing:
        fixes.append(f"missing-files-detected: {', '.join(missing)} (requires prompt repair)")

    return fixes
