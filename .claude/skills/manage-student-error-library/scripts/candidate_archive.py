#!/usr/bin/env python3
"""Append-only Candidate Archive for Wuli's Memory-Evolve layer.

The archive records teacher actions, agent candidate outcomes, deterministic
build results, and evaluator summaries.  It deliberately stores compact,
sanitized metadata instead of full prompts, API keys, source images, or raw
student artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import kb

SCHEMA_VERSION = 1
ENTRY_ARCHIVE = "candidate-archive.jsonl"
LIBRARY_ARCHIVE = "indexes/candidate-archive.jsonl"
LIBRARY_ENTRY_ID = "__library__"
SECRET_KEYS = {"api_key", "authorization", "token", "password", "secret"}
MAX_TEXT = 1000
MAX_LIST = 30


def _short_text(value: Any, limit: int = MAX_TEXT) -> str:
    text = str(value or "")
    text = re.sub(r"[\r\n\t]+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(secret in lowered for secret in SECRET_KEYS):
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = sanitize(item)
        return cleaned
    if isinstance(value, list):
        items = value[:MAX_LIST]
        cleaned = [sanitize(item) for item in items]
        if len(value) > MAX_LIST:
            cleaned.append(f"... {len(value) - MAX_LIST} more")
        return cleaned
    if isinstance(value, str):
        return _short_text(value)
    return value


def _event_id(entry_id: str, event: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(event, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"{entry_id}-{digest[:12]}"


def _append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _status_from_payload(status: str, failure_reasons: list[str]) -> str:
    if status in {"completed", "approved", "saved", "published-local", "prepared", "ok", "passed", "delivered"}:
        return "succeeded" if not failure_reasons else "needs-review"
    if status in {"failed", "blocked", "unavailable"}:
        return "failed"
    if status in {"queued", "requested", "awaiting-agent", "revision-requested", "needs-review"}:
        return "pending"
    return "recorded"


def append_event(
    library: Path,
    entry: Path,
    *,
    task_type: str,
    actor: str,
    event_type: str,
    status: str,
    summary: str = "",
    request: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
    changed_files: list[str] | None = None,
    failure_reasons: list[str] | None = None,
) -> dict[str, Any]:
    failure_reasons = [_short_text(item, 500) for item in (failure_reasons or [])]
    evaluation_summary = sanitize(evaluation or {})
    payload = {
        "schema_version": SCHEMA_VERSION,
        "entry_id": entry.name,
        "task_type": task_type,
        "actor": actor,
        "event_type": event_type,
        "status": _status_from_payload(status, failure_reasons),
        "raw_status": status,
        "summary": _short_text(summary),
        "created_at": kb.now_iso(),
        "changed_files": sanitize(changed_files or []),
        "failure_reasons": failure_reasons,
        "request": sanitize(request or {}),
        "result": sanitize(result or {}),
        "evaluation": evaluation_summary,
    }
    payload["event_id"] = _event_id(entry.name, payload)
    _append_jsonl(entry / ENTRY_ARCHIVE, payload)
    _append_jsonl(library / LIBRARY_ARCHIVE, payload)
    return payload


def append_library_event(
    library: Path,
    *,
    task_type: str,
    actor: str,
    event_type: str,
    status: str,
    summary: str = "",
    request: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    evaluation: dict[str, Any] | None = None,
    changed_files: list[str] | None = None,
    failure_reasons: list[str] | None = None,
) -> dict[str, Any]:
    failure_reasons = [_short_text(item, 500) for item in (failure_reasons or [])]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "entry_id": LIBRARY_ENTRY_ID,
        "task_type": task_type,
        "actor": actor,
        "event_type": event_type,
        "status": _status_from_payload(status, failure_reasons),
        "raw_status": status,
        "summary": _short_text(summary),
        "created_at": kb.now_iso(),
        "changed_files": sanitize(changed_files or []),
        "failure_reasons": failure_reasons,
        "request": sanitize(request or {}),
        "result": sanitize(result or {}),
        "evaluation": sanitize(evaluation or {}),
    }
    payload["event_id"] = _event_id(LIBRARY_ENTRY_ID, payload)
    _append_jsonl(library / LIBRARY_ARCHIVE, payload)
    return payload


def read_events(entry: Path) -> list[dict[str, Any]]:
    path = entry / ENTRY_ARCHIVE
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def read_library_events(library: Path) -> list[dict[str, Any]]:
    path = library / LIBRARY_ARCHIVE
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", type=Path, default=Path.cwd() / "student-error-library")
    parser.add_argument("entry_id")
    parser.add_argument("--task-type", default="manual.note")
    parser.add_argument("--actor", default="teacher")
    parser.add_argument("--event-type", default="note")
    parser.add_argument("--status", default="recorded")
    parser.add_argument("--summary", default="")
    args = parser.parse_args()
    library = args.library.expanduser().resolve()
    entry = library / "entries" / args.entry_id
    if not entry.exists():
        raise FileNotFoundError(args.entry_id)
    event = append_event(
        library,
        entry,
        task_type=args.task_type,
        actor=args.actor,
        event_type=args.event_type,
        status=args.status,
        summary=args.summary,
    )
    print(json.dumps(event, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
