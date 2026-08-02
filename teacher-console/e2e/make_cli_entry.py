#!/usr/bin/env python3
"""Construct a minimal, privacy-free entry for the visual E2E CLI scenarios (C5.3/C5.4).

Mirrors ``kb.ingest_one``'s on-disk shape (record.json / ocr.json / problem.md
/ assets/original.png) with a caller-supplied entry id, so the CLI entry can
never collide with the web-uploaded entry in the same temp library (dedupe is
content-hash based, so identical fixture bytes would otherwise resolve to the
same entry id). It runs nothing else: ``entry_visual_extract.py`` is the real
next step exercised by the scenario.

Usage: make_cli_entry.py <library> <entry-id> <image-path>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))

import kb  # noqa: E402


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: make_cli_entry.py <library> <entry-id> <image-path>", file=sys.stderr)
        return 2
    library = Path(sys.argv[1]).resolve()
    entry_id = sys.argv[2]
    image = Path(sys.argv[3]).resolve()
    if not image.is_file():
        print(f"image not found: {image}", file=sys.stderr)
        return 2
    entry = (library / "entries" / entry_id).resolve()
    try:
        entry.relative_to((library / "entries").resolve())
    except ValueError:
        print("entry id escapes the library", file=sys.stderr)
        return 2
    if entry.exists():
        print(f"entry already exists: {entry_id}", file=sys.stderr)
        return 2
    assets = entry / "assets"
    assets.mkdir(parents=True)
    stored = assets / "original.png"
    stored.write_bytes(image.read_bytes())
    kb.write_json(
        entry / "record.json",
        {
            "schema_version": 1,
            "id": entry_id,
            "kind": "error",
            "status": "needs-review",
            "answer_status": "pending",
            "title": "E2E CLI visual extract",
            "subject": "高中物理",
            "knowledge_points": ["测试"],
            "error_types": ["待确认"],
            "source": {
                "sha256": kb.sha256_file(stored),
                "source_type": "png",
                "stored_files": ["assets/original.png"],
            },
            "ocr": {"engine": "none", "average_confidence": 0.0, "review_required": True},
            "source_review": {"status": "needs-review", "method": "human"},
        },
    )
    kb.write_json(entry / "ocr.json", {"engine": "none", "text": "", "average_confidence": 0.0})
    kb.write_text(entry / "problem.md", kb.problem_draft(entry_id, ["assets/original.png"], ""))
    print(json.dumps({"status": "created", "entry_id": entry_id, "entry": str(entry)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
