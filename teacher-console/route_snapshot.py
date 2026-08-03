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
ROUTE_EXECUTION_PLAN_SCHEMA = "wuli.route-execution-plan.v1"

# Expected stage sequences per planned solver route (A2.1).
CORE_FIRST_STAGES = (
    "structured-generation",
    "core-gate",
    "physics-quality-gate",
    "deterministic-teaching-render",
    "render-fidelity-gate",
    "authoritative-review",
)
W3_STAGES = (
    "decompose",
    "solver-a",
    "claim-verifier",
    "proof-aggregation",
    "renderer-selection",
    "render",
    "authoritative-review",
)


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


def build_route_execution_plan(
    *,
    library: Path,
    core_config: dict | None = None,
    w3r_config: dict | None = None,
) -> dict:
    """Express the planned solver route and renderer mode explicitly (A2.1).

    Never collapses to a single "deep analysis" boolean: ``core + w3r off``,
    ``w3 + w3r shadow`` and ``w3 + legacy renderer`` are distinct plans.
    ``w3r_config`` is expected to be already normalized (invalid configs fail
    closed to ``off`` by ``analysis_routing.normalize_w3r_config``).
    """
    core_config = core_config if isinstance(core_config, dict) else {}
    w3r_config = w3r_config if isinstance(w3r_config, dict) else {}
    solver_route = "w3" if str(core_config.get("mode", "")).strip() == "legacy-adaptive" else "core"
    w3r_mode = str(w3r_config.get("mode", "off")).strip() or "off"
    if w3r_mode not in {"shadow", "gray", "default"}:
        w3r_mode = "off"
    if solver_route == "w3" and w3r_mode == "shadow":
        renderer_mode = "w3r-shadow"
    elif w3r_mode in {"gray", "default"} and solver_route == "w3":
        renderer_mode = "w3r"
    else:
        renderer_mode = "legacy"
    expected_stages = list(W3_STAGES if solver_route == "w3" else CORE_FIRST_STAGES)
    return {
        "schema": ROUTE_EXECUTION_PLAN_SCHEMA,
        "planned_solver_route": solver_route,
        "planned_renderer_mode": renderer_mode,
        "w3r_mode": w3r_mode,
        "expected_stages": expected_stages,
        "config_digest": route_config_digest(library),
        "canonical_write_policy": "promote-after-teacher-review",
        "created_at": now_iso(),
    }


__all__ = [
    "ROUTE_SNAPSHOT_SCHEMA",
    "ROUTE_EXECUTION_PLAN_SCHEMA",
    "CORE_FIRST_STAGES",
    "W3_STAGES",
    "build_route_snapshot",
    "build_route_execution_plan",
    "route_config_digest",
    "route_snapshot_is_stale",
    "route_snapshot_summary",
]
