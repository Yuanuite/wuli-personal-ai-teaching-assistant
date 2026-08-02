#!/usr/bin/env python3
"""Route snapshot contract and helpers (closure C3.1/C3.2/C3.3).

A :data:`ROUTE_SNAPSHOT_SCHEMA` freezes the requested/resolved model identity
and the configuration digest at enqueue time. Execution fails closed when the
registry or production route changed after the snapshot was created, so a
job can never silently run a different model than the queue advertised.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

ROUTE_SNAPSHOT_SCHEMA = "wuli.route-snapshot.v1"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _digest(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def route_config_digest(library: Path) -> str:
    """Digest of the model registry plus the production analysis route config."""
    digest = hashlib.sha256()
    for name in ("model-registry.json", "analysis-production-routing.json"):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_digest(library / "config" / name).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def build_route_snapshot(
    *,
    kind: str,
    routing_tier: str,
    requested_model_id: str,
    config: dict | None,
    library: Path,
) -> dict:
    """Build an immutable route snapshot from the enqueue-time resolution."""
    config = config if isinstance(config, dict) else {}
    return {
        "schema": ROUTE_SNAPSHOT_SCHEMA,
        "kind": str(kind).strip(),
        "routing_tier": str(routing_tier).strip(),
        "requested_model_id": str(requested_model_id or "auto").strip(),
        "resolved_model_id": str(config.get("id", "")).strip(),
        "provider": str(config.get("provider", "")).strip(),
        "upstream_model": str(config.get("model", "")).strip(),
        "config_digest": route_config_digest(library),
        "created_at": now_iso(),
    }


def route_snapshot_is_stale(snapshot: dict, library: Path) -> bool:
    """True when the frozen config digest no longer matches the disk state."""
    if not isinstance(snapshot, dict) or snapshot.get("schema") != ROUTE_SNAPSHOT_SCHEMA:
        return True
    return str(snapshot.get("config_digest", "")) != route_config_digest(library)


def route_snapshot_summary(snapshot: dict) -> dict:
    """Public-safe summary for the job serializer and UI (no secrets)."""
    if not isinstance(snapshot, dict):
        return {}
    return {
        "schema": snapshot.get("schema"),
        "kind": snapshot.get("kind"),
        "routing_tier": snapshot.get("routing_tier"),
        "requested_model_id": snapshot.get("requested_model_id"),
        "resolved_model_id": snapshot.get("resolved_model_id"),
        "provider": snapshot.get("provider"),
        "upstream_model": snapshot.get("upstream_model"),
        "config_digest": snapshot.get("config_digest"),
        "created_at": snapshot.get("created_at"),
    }


__all__ = [
    "ROUTE_SNAPSHOT_SCHEMA",
    "build_route_snapshot",
    "route_config_digest",
    "route_snapshot_is_stale",
    "route_snapshot_summary",
]
