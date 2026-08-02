#!/usr/bin/env python3
"""Thin CLI entry for registry-routed visual extraction (work-tree A3.3/C2.3).

Runs the SAME application service as the web upload path — shared
``run_visual_extract()`` → registry ``defaults.vision`` route → staging — and
produces the same ``VisualExtractOutcome.v1``, ``visual-facts.json`` /
``visual-facts-gate.json`` / ``source-review`` artifacts and call ledger. It
never grants source approval.

The legacy sidecar adapter remains available as an explicit override and is
recorded as ``legacy-adapter``; it never becomes a second result schema.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
sys.path.insert(0, str(CONSOLE))
sys.path.insert(0, str(SKILL_SCRIPTS))

import kb  # noqa: E402
from visual_application import run_visual_extract  # noqa: E402


def _fail(status: int, error: str) -> int:
    print(json.dumps({"status": "failed", "error": str(error)[:500]}, ensure_ascii=False))
    return status


def _run_legacy_adapter(entry: Path, command_text: str, locality: str, allow_remote: bool) -> int:
    """Compat path: explicit legacy adapter, recorded as legacy-adapter."""
    import source_review as sr

    try:
        report = sr.run_adapter(entry, command_text, locality, allow_remote)
    except Exception as exc:  # noqa: BLE001 - preserve safe human gate
        return _fail(3, f"legacy adapter failed: {exc}")
    summary = {
        "status": "completed",
        "method": "legacy-adapter",
        "source_review_status": report.get("status"),
        "entry_id": entry.name,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry_id", help="entry id under <library>/entries")
    parser.add_argument("--library", default=str(ROOT / "student-error-library"))
    parser.add_argument("--tier", default="auto", choices=["auto", "economy", "expert"])
    parser.add_argument("--model", default=None, help="explicit vision model id (must declare vision trait)")
    parser.add_argument(
        "--legacy-adapter",
        default="",
        help="explicit legacy sidecar command override; records method=legacy-adapter",
    )
    parser.add_argument("--adapter-locality", default="local", choices=["local", "remote"])
    args = parser.parse_args()

    library = Path(args.library)
    entry = (library / "entries" / args.entry_id).resolve()
    try:
        entry.relative_to((library / "entries").resolve())
    except ValueError:
        return _fail(2, "entry path escapes the library")
    if not entry.is_dir():
        return _fail(2, f"entry not found: {args.entry_id}")

    config = kb.load_json(library / "config.json", {})
    allow_remote = bool(config.get("privacy", {}).get("allow_remote_visual_review", False))

    if args.legacy_adapter:
        return _run_legacy_adapter(entry, args.legacy_adapter, args.adapter_locality, allow_remote)

    outcome = run_visual_extract(
        entry,
        library=library,
        routing_tier=args.tier,
        model_id=args.model,
        allow_remote=allow_remote,
    )
    if outcome.get("status") != "completed":
        print(json.dumps({"status": "failed", "error": outcome.get("message", "")}, ensure_ascii=False))
        return 3
    print(json.dumps(
        {
            "status": "completed",
            "entry_id": entry.name,
            "outcome_schema": outcome.get("schema"),
            "source_review_status": "needs-review",
            "visual_gate_status": outcome.get("visual_gate_status"),
            "model_id": outcome.get("model_id"),
            "upstream_model": outcome.get("upstream_model"),
            "uncertainties": outcome.get("uncertainties"),
            "artifacts": ["visual-facts.json", "visual-facts-gate.json", "source-review.md", "source-review.json"],
        },
        ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
