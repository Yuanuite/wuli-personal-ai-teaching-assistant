#!/usr/bin/env python3
"""Shared visual-extract application service (closure C2.1/C2.2/C2.3/C2.5).

Web upload and the thin CLI both call :func:`run_visual_extract` so the
registry route, privacy gate, extraction, staging and call ledger stay one
observable, fail-closed path. The Agent Gateway keeps provider execution;
this module never grants source approval.
"""

from __future__ import annotations

import time
from pathlib import Path

import kb
import source_review
import visual_source_review
from agent_gateway import AgentGateway
from runtime_environment import resolved_environment

OUTCOME_SCHEMA = "wuli.visual-extract-outcome.v1"
CONTRACT = "wuli.visual-facts.v1"


def _failed_outcome(entry_id: str, stage: str, failure_type: str, message: str) -> dict:
    return {
        "schema": OUTCOME_SCHEMA,
        "status": "failed",
        "stage": stage,
        "entry_id": entry_id,
        "model_id": "",
        "provider": "",
        "upstream_model": "",
        "contract": CONTRACT,
        "elapsed_seconds": 0.0,
        "usage": {},
        "input_fingerprint": "",
        "output_fingerprint": "",
        "failure_type": failure_type,
        "message": str(message)[:500],
    }


def _redact_ledger(value: dict) -> dict:
    """Strip fields that must never leave the entry: no data URLs, keys, or
    absolute temp paths; only safe summary fields survive."""
    allow = {
        "schema",
        "status",
        "stage",
        "entry_id",
        "model_id",
        "provider",
        "upstream_model",
        "contract",
        "elapsed_seconds",
        "usage",
        "input_fingerprint",
        "output_fingerprint",
        "failure_type",
        "message",
        "requested_tier",
    }
    return {key: value[key] for key in allow if key in value}


def run_visual_extract(
    entry: Path,
    *,
    library: Path,
    routing_tier: str = "auto",
    model_id: str | None = None,
    allow_remote: bool | None = None,
    gateway: AgentGateway | None = None,
    ledger_path: Path | None = None,
) -> dict:
    """Run the registry-routed visual extraction and stage the result.

    Returns a :data:`OUTCOME_SCHEMA` dict. On success the entry gains
    ``visual-facts.json`` / ``visual-facts-gate.json`` / updated
    ``source-review`` artifacts and stays at the teacher source-review gate.
    """
    entry = Path(entry)
    started = time.monotonic()
    config = kb.load_json(library / "config.json", {})
    if allow_remote is None:
        allow_remote = bool(config.get("privacy", {}).get("allow_remote_visual_review", False))
    record = kb.load_json(entry / "record.json", {})
    ocr = kb.load_json(entry / "ocr.json", {})
    try:
        payload = source_review.review_payload(entry)
        fingerprint = "sha256:" + source_review.input_digest(entry, record, ocr)
    except Exception as exc:  # noqa: BLE001 - fail closed with a durable reason
        outcome = _failed_outcome(entry.name, "payload-failed", "invalid_entry_state", str(exc))
        _persist_ledger(entry, ledger_path, outcome)
        return outcome

    effective_gateway = gateway or AgentGateway(environment_resolver=lambda: resolved_environment(library))
    try:
        extraction = effective_gateway.extract_visual_facts(
            payload,
            fingerprint,
            allow_remote=allow_remote,
            routing_tier=routing_tier,
            model_id=model_id,
        )
    except Exception as exc:  # noqa: BLE001 - provider/privacy/protocol failure
        outcome = _failed_outcome(entry.name, "extract-failed", "visual_extraction_failed", str(exc))
        outcome["input_fingerprint"] = fingerprint
        _persist_ledger(entry, ledger_path, outcome)
        return outcome

    try:
        visual_source_review.stage_visual_extraction(entry, extraction)
    except Exception as exc:  # noqa: BLE001 - keep the human gate intact
        outcome = _failed_outcome(entry.name, "staging-failed", "visual_facts_staging_failed", str(exc))
        outcome["input_fingerprint"] = fingerprint
        _persist_ledger(entry, ledger_path, outcome)
        return outcome

    trace = extraction.get("trace") if isinstance(extraction.get("trace"), dict) else {}
    visual_facts = extraction.get("visual_facts") if isinstance(extraction.get("visual_facts"), dict) else {}
    outcome = {
        "schema": OUTCOME_SCHEMA,
        "status": "completed",
        "stage": "extracted",
        "entry_id": entry.name,
        "model_id": str(trace.get("model_id", "")).strip(),
        "provider": str(trace.get("provider", "")).strip(),
        "upstream_model": str(trace.get("upstream_model", "")).strip(),
        "contract": CONTRACT,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "usage": {},
        "input_fingerprint": fingerprint,
        "output_fingerprint": str(visual_facts.get("fingerprint", "")).strip(),
        "failure_type": "",
        "message": "visual facts staged for teacher review",
        "requested_tier": routing_tier,
        "visual_gate_status": str((extraction.get("gate_result") or {}).get("status", "")).strip(),
        "uncertainties": len(visual_facts.get("uncertainties") or []),
    }
    _persist_ledger(entry, ledger_path, outcome)
    kb.rebuild_index(library)
    return outcome


def _persist_ledger(entry: Path, ledger_path: Path | None, outcome: dict) -> None:
    """Persist the private visual call ledger (C2.5) next to the entry."""
    target = ledger_path or (entry / "visual-extract-request.json")
    try:
        kb.write_json(target, _redact_ledger(outcome))
    except Exception:  # noqa: BLE001 - a ledger failure must not break lifecycle
        pass


def make_gateway(library: Path, environ: dict[str, str] | None = None) -> AgentGateway:
    return AgentGateway(
        environ=environ,
        environment_resolver=lambda: resolved_environment(library),
    )


__all__ = ["run_visual_extract", "make_gateway", "OUTCOME_SCHEMA", "CONTRACT"]
