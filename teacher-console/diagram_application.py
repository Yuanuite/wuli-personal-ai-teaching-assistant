#!/usr/bin/env python3
"""Shared static-diagram application service (closure C4.1/C4.5/C4.6).

Web ``POST /api/entries/<id>/build-diagram`` and the thin CLI
``entry_action.py build-diagram`` both call :func:`build_diagram`, which gates
on approved source + existing answers, runs the DeepSeek scene → deterministic
SVG → hard-gate chain with at most one bounded hard-gate patch, then runs one
non-blocking MiMo soft review on a safe raster. A soft review has suggestion
rights only: at most one bounded scene patch may follow, and a changed diagram
invalidates the previous answer approval through the shared answer digest.
"""

from __future__ import annotations

import subprocess
import urllib.request
from pathlib import Path

import kb

BUILD_SCHEMA = "wuli.diagram-build.v1"


def _result(entry: Path, **payload) -> dict:
    base = {
        "schema": BUILD_SCHEMA,
        "entry_id": entry.name,
        "status": "blocked",
    }
    base.update(payload)
    return base


def _run_raster_render(svg_path: Path, png_path: Path, node: str = "node") -> bool:
    """Render the deterministic SVG to a privacy-free raster via playwright."""
    script = Path(__file__).resolve().parent / "scripts" / "svg_to_raster.mjs"
    if not script.is_file():
        return False
    try:
        completed = subprocess.run(
            [node, str(script), str(svg_path), str(png_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and png_path.is_file()


def _soft_review(entry: Path, library: Path) -> dict:
    """One non-blocking MiMo soft review on a safe raster (C4.4/C4.5).

    Suggestion-only: never writes facts, answers, SVG, or approval fields.
    """
    from diagram_visual_review import run_diagram_visual_review
    from model_registry import model_config_for_trait

    try:
        config = model_config_for_trait("vision", routing_tier="auto")
    except Exception as exc:  # noqa: BLE001 - soft review must not break diagram
        return {"status": "blocked", "reason": f"no vision route: {str(exc)[:120]}"}
    if not isinstance(config, dict):
        return {"status": "blocked", "reason": "no vision route"}
    svg_path = entry / "assets" / "explanatory.svg"
    if not svg_path.is_file():
        return {"status": "skipped", "reason": "no rendered svg"}
    png_path = entry / "assets" / "explanatory-review.png"
    try:
        png_path.unlink(missing_ok=True)
    except OSError:
        pass
    if not _run_raster_render(svg_path, png_path):
        return {"status": "unavailable", "reason": "raster render unavailable (node/playwright)"}
    privacy = kb.load_json(library / "config.json", {}).get("privacy", {})
    try:
        review = run_diagram_visual_review(
            config,
            str(png_path),
            urlopen=urllib.request.urlopen,
            allow_remote=bool(privacy.get("allow_remote_visual_review", False)),
        )
    except Exception as exc:  # noqa: BLE001 - review failure is non-blocking
        return {"status": "failed", "reason": str(exc)[:200]}
    return review


def _soft_patch(entry: Path, review: dict) -> dict:
    """At most one bounded scene patch driven by actionable soft suggestions.

    Mirrors the hard-gate repair chain: the current scene is the base payload,
    soft suggestions become diagnostics, and the patch validator re-runs every
    hard gate. Immutable physics/facts are enforced by that validator; a failed
    or non-improving patch keeps the original approved SVG.
    """
    if review.get("status") != "passed":
        return {"status": "skipped", "reason": "soft review did not pass"}
    suggestions = review.get("suggestions") or []
    actionable = [
        item for item in suggestions
        if isinstance(item, dict) and str(item.get("severity", "")) == "warning"
    ]
    if not actionable:
        return {"status": "skipped", "reason": "no actionable soft suggestions"}
    scene_path = entry / "physics-diagram-scene.json"
    if not scene_path.is_file():
        return {"status": "skipped", "reason": "no current scene to patch"}
    candidate = kb.load_json(scene_path, {})
    if not isinstance(candidate, dict) or not candidate:
        return {"status": "skipped", "reason": "current scene is empty"}

    from physics_diagram import materialize_patch
    from server import (
        model_config_for_task,
        physics_diagram_patch_task,
        resolve_model_id_for_task,
        run_agent_gateway,
        validate_physics_diagram_candidate,
    )

    diagnostics = {
        "diagnostics": [
            {
                "code": str(item.get("code", "readability")),
                "severity": "warning",
                "message": str(item.get("message", "")),
            }
            for item in actionable
        ]
    }
    try:
        model_config = model_config_for_task(
            "analysis.generate", resolve_model_id_for_task("analysis.generate", "auto", None), "auto"
        )
    except Exception:  # noqa: BLE001 - a missing config must not abort the patch
        model_config = None
    try:
        patch_task = physics_diagram_patch_task(
            entry, candidate, diagnostics, routing_tier="auto", model_config=model_config
        )
        result = run_agent_gateway(
            entry,
            patch_task,
            lambda staging, changed: validate_physics_diagram_candidate(staging, changed, entry),
            materializer=lambda staging, payload: materialize_patch(
                staging, payload, base_payload=candidate, model_config=model_config or {}
            ),
            bounded_failure_repair=False,
        )
    except Exception as exc:  # noqa: BLE001 - keep the original approved SVG
        return {"status": "failed", "reason": str(exc)[:200]}
    return {
        "status": "completed" if result.get("status") == "completed" else "accepted-with-warnings",
        "attempt_count": 1,
        "patch_policy": "single-bounded-soft-patch",
        "result_status": result.get("status"),
    }


def build_diagram(
    entry: Path,
    *,
    library: Path,
    routing_tier: str = "auto",
    model_config: dict | None = None,
    run_gateway=None,
    enable_soft_review: bool = True,
) -> dict:
    """Run the explicit static-diagram action (C4.1).

    Only runs when the source is approved and layered answers exist; a missing
    diagram never blocks answer review or delivery. A changed
    ``assets/explanatory.svg`` invalidates the previous answer approval through
    the shared answer digest (C4.6).
    """
    entry = Path(entry)
    record = kb.load_json(entry / "record.json", {})
    if record.get("source_review", {}).get("status") != "passed":
        return _result(entry, status="blocked", stage="gate", reason="source review is not approved")
    for name in ("student-solution.md", "teacher-solution.md", "solution.md"):
        if not (entry / name).is_file():
            return _result(entry, status="blocked", stage="gate", reason=f"answer layer missing: {name}")
    if not (entry / "visual-facts.json").is_file():
        return _result(
            entry,
            status="blocked",
            stage="gate",
            reason="visual-facts.json missing (run visual extract first)",
        )

    from server import run_physics_diagram_gateway  # lazy: avoid circular import

    gateway = run_gateway or (
        lambda e, **kw: run_physics_diagram_gateway(e, **kw)
    )
    result = gateway(
        entry,
        routing_tier=routing_tier,
        model_config=model_config,
        canonical_entry=entry,
    )
    summary = {
        "schema": BUILD_SCHEMA,
        "entry_id": entry.name,
        "status": result.get("status", "failed"),
        "stage": "diagram-gateway",
        "diagram_repair": result.get("diagram_repair", {}),
        "model_id": result.get("model_id", ""),
        "provider": result.get("provider", ""),
        "message": str(result.get("message", ""))[:500],
    }
    if result.get("status") != "completed":
        summary["failure_type"] = result.get("failure_type", "diagram_failed")
        _persist_build(entry, summary)
        return summary

    soft_review: dict = {}
    if enable_soft_review:
        soft_review = _soft_review(entry, library)
        summary["soft_review"] = {
            key: soft_review[key]
            for key in ("status", "reason", "model_id", "upstream_model")
            if key in soft_review
        }
        summary["soft_review"]["suggestion_count"] = len(soft_review.get("suggestions") or [])
        if soft_review.get("status") == "passed" and (
            any(
                isinstance(item, dict) and str(item.get("severity", "")) == "warning"
                for item in (soft_review.get("suggestions") or [])
            )
        ):
            patch = _soft_patch(entry, soft_review)
            summary["soft_patch"] = patch
            if patch.get("status") == "completed":
                summary["status"] = "completed"
                summary["patch_note"] = "软评审后一次受限 Patch 通过硬门"
    summary["status"] = "completed"
    summary["artifacts"] = [
        "physics-diagram-scene.json",
        "assets/explanatory.svg",
        "physics-diagram-gate.json",
        "svg-provenance.json",
    ]
    _persist_build(entry, summary)
    return summary


def _persist_build(entry: Path, summary: dict) -> None:
    try:
        kb.write_json(entry / "diagram-build.json", summary)
    except Exception:  # noqa: BLE001 - private build record must not break action
        pass


__all__ = ["build_diagram", "BUILD_SCHEMA"]
