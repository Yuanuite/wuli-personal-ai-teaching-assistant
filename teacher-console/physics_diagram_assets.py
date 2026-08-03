#!/usr/bin/env python3
"""Versioned semantic components and deterministic physics-model projection.

The catalog stores meaning and composition rules, not frozen SVG fragments.
Coordinates produced by an Agent are layout hints; when a reviewed
``physics-model.json`` exists, model-owned trajectories and boundary topology
are compiled here before the semantic gate and renderer run.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

CATALOG_SCHEMA = "wuli.physics-diagram-component-catalog.v1"

COMPONENTS: tuple[dict[str, Any], ...] = (
    {
        "id": "parallel-plates",
        "meaning": "two ordered physical boundaries with a field gap",
        "compiler_owned": ["relative-order", "parallelism", "boundary-labels"],
    },
    {
        "id": "particle-launch",
        "meaning": "particle at a reviewed start event with initial direction",
        "compiler_owned": ["start-position"],
    },
    {
        "id": "field-region",
        "meaning": "electric, magnetic, or combined physical region",
        "compiler_owned": ["region-topology"],
    },
    {
        "id": "trajectory-segment",
        "meaning": "one event-to-event motion segment",
        "compiler_owned": ["points", "continuity", "start-event", "end-event"],
    },
    {
        "id": "event-marker",
        "meaning": "a switch, collision, or stop event on a trajectory",
        "compiler_owned": ["event-position", "event-order"],
    },
    {
        "id": "square-wave",
        "meaning": "piecewise-constant field as a function of time",
        "compiler_owned": ["zero-line", "levels", "switch-order"],
    },
    {
        "id": "coordinate-frame",
        "meaning": "labeled axes for a 2D view or 3D projection",
        "compiler_owned": ["axis-orientation"],
    },
)


def _fingerprint(value: Any) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def component_catalog(signals: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded catalog slice needed by this problem."""
    selected = ["field-region"]
    if signals.get("particle_motion"):
        selected.extend(["particle-launch", "trajectory-segment", "event-marker"])
    if signals.get("periodic_field"):
        selected.append("square-wave")
    if signals.get("spatial_projection"):
        selected.append("coordinate-frame")
    payload = {
        "schema": CATALOG_SCHEMA,
        "selection_policy": "Agent selects teaching intent; compiler owns model-derived geometry.",
        "selected_component_ids": list(dict.fromkeys(selected)),
        "components": [copy.deepcopy(item) for item in COMPONENTS if item["id"] in selected],
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def _point3(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        return None
    return float(value[0]), float(value[1]), float(value[2])


def _segment_points(segment: dict[str, Any]) -> list[tuple[float, float, float]]:
    geometry = segment.get("geometry")
    if not isinstance(geometry, dict):
        return []
    if geometry.get("path_kind") == "points":
        return [point for raw in geometry.get("points", []) if (point := _point3(raw)) is not None]
    if geometry.get("path_kind") != "arc3d":
        return []
    center = _point3(geometry.get("center"))
    basis_u = _point3(geometry.get("basis_u"))
    basis_v = _point3(geometry.get("basis_v"))
    radius = geometry.get("radius")
    start = geometry.get("start_deg")
    end = geometry.get("end_deg")
    if (
        center is None
        or basis_u is None
        or basis_v is None
        or isinstance(radius, bool)
        or not isinstance(radius, (int, float))
        or isinstance(start, bool)
        or not isinstance(start, (int, float))
        or isinstance(end, bool)
        or not isinstance(end, (int, float))
    ):
        return []
    sweep = float(end) - float(start)
    steps = max(4, min(24, int(abs(sweep) / 15) + 1))
    points: list[tuple[float, float, float]] = []
    for index in range(steps + 1):
        angle = math.radians(float(start) + sweep * index / steps)
        points.append(
            tuple(
                center[axis] + float(radius) * (math.cos(angle) * basis_u[axis] + math.sin(angle) * basis_v[axis])
                for axis in range(3)
            )
        )
    return points


def _project(point: tuple[float, float, float]) -> tuple[float, float]:
    """Top-view teaching projection for magnetic curvature: x right, y up.

    Depth is explained by the z-axis/field labels.  Mixing z into the planar
    coordinates makes a circular magnetic orbit look non-circular and hides
    the curvature reversal that the diagram is meant to teach.
    """
    x, y, _z = point
    return x, y


def _normalizer(points: list[tuple[float, float]]):
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    scale = min(72.0 / span_x, 64.0 / span_y)
    center_x = (min_x + max_x) / 2
    center_y = (min_y + max_y) / 2

    def normalize(point: tuple[float, float]) -> dict[str, float]:
        return {
            "x": round(50 + scale * (point[0] - center_x), 4),
            # SVG y grows downward, while the physics model's y grows upward.
            "y": round(50 - scale * (point[1] - center_y), 4),
        }

    return normalize


def _circular_projection(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]] | None:
    """Return start/mid/end when sampled x-y points lie on one circle."""
    if len(points) < 3:
        return None
    selected = [points[0], points[len(points) // 2], points[-1]]
    (x1, y1), (x2, y2), (x3, y3) = [_project(point) for point in selected]
    determinant = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(determinant) < 1e-9:
        return None
    ux = (
        (x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)
    ) / determinant
    uy = (
        (x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)
    ) / determinant
    radius = math.hypot(x1 - ux, y1 - uy)
    if radius <= 1e-9:
        return None
    relative_error = max(
        abs(math.hypot(x - ux, y - uy) - radius) / radius for x, y in (_project(point) for point in points)
    )
    return selected if relative_error <= 0.015 else None


def _short_segment_label(segment: dict[str, Any]) -> str:
    case_ids = [str(item) for item in segment.get("case_ids", []) if str(item).strip()]
    case = "（1）" if "part1" in case_ids else "（2）" if "part2" in case_ids else ""
    raw = str(segment.get("label", "模型轨迹段")).strip()
    field = raw.split("：", 1)[0].strip()
    angle = next(iter(re.findall(r"(?:\d+°|半圆|四分之一圆)", raw)), "")
    return " ".join(item for item in (case, field, angle) if item)[:80]


def _event_label(event: dict[str, Any]) -> str:
    label = str(event.get("label", ""))
    event_id = str(event.get("id", ""))
    case = "(1)" if event_id.startswith("p1-") else "(2)" if event_id.startswith("p2-") else ""
    if "电场反向" in label:
        return f"{case} E换"
    if "磁场反向" in label:
        return f"{case} B换"
    if "P 板" in label or "打在 P" in label:
        # The plate and trajectory arrow already show the collision; another
        # labeled circle obscures the perpendicular-hit geometry.
        return ""
    return ""


def compile_model_scene(
    scene: dict[str, Any], physics_model: dict[str, Any] | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replace model-owned geometry while preserving Agent-owned teaching intent."""
    if not isinstance(physics_model, dict):
        return copy.deepcopy(scene), {"status": "not-applicable", "model_fingerprint": ""}
    raw_segments = physics_model.get("trajectory", {}).get("segments", [])
    segments = [item for item in raw_segments if isinstance(item, dict)] if isinstance(raw_segments, list) else []
    sampled = [(segment, _segment_points(segment)) for segment in segments]
    sampled = [(segment, points) for segment, points in sampled if len(points) >= 2]
    if not sampled:
        return copy.deepcopy(scene), {
            "status": "not-applicable",
            "model_fingerprint": _fingerprint(physics_model),
        }

    compiled = copy.deepcopy(scene)
    panel_ids = [item.get("id") for item in compiled.get("panels", []) if isinstance(item, dict)]
    motion_panel = "motion" if "motion" in panel_ids else (panel_ids[0] if panel_ids else "")
    if not motion_panel:
        return compiled, {"status": "invalid", "reason": "model projection requires a panel"}

    projected = [_project(point) for _, points in sampled for point in points]
    event_model = physics_model.get("event_model", {})
    timeline = event_model.get("timeline", []) if isinstance(event_model, dict) else []
    event_positions = (
        [point for item in timeline if isinstance(item, dict) if (point := _point3(item.get("position"))) is not None]
        if isinstance(timeline, list)
        else []
    )
    boundary_regions = (
        [
            item
            for item in physics_model.get("regions", [])
            if isinstance(item, dict)
            and isinstance(item.get("field"), dict)
            and item["field"].get("kind") == "boundary"
        ]
        if isinstance(physics_model.get("regions"), list)
        else []
    )
    boundary_origins = [
        point
        for item in boundary_regions
        if isinstance(item.get("shape"), dict)
        if (point := _point3(item["shape"].get("origin"))) is not None
    ]
    geometry_centers = [
        point
        for segment, _points in sampled
        if isinstance(segment.get("geometry"), dict)
        if (point := _point3(segment["geometry"].get("center"))) is not None
    ]
    normalize = _normalizer(
        projected + [_project(point) for point in event_positions + boundary_origins + geometry_centers]
    )

    inherited_fact_ids = list(
        dict.fromkeys(
            fact_id
            for path in compiled.get("paths", [])
            if isinstance(path, dict) and path.get("kind") == "trajectory"
            for fact_id in path.get("fact_ids", [])
            if isinstance(fact_id, str)
        )
    )
    is_spatial_model = physics_model.get("model_type") == "piecewise-field-particle-3d"
    other_paths = [
        path
        for path in compiled.get("paths", [])
        if (
            not isinstance(path, dict)
            or (
                path.get("kind") != "trajectory"
                and not (
                    is_spatial_model
                    and path.get("kind") == "axis"
                    and str(path.get("label", "")).strip().lower().startswith("z")
                )
            )
        )
    ]
    model_paths: list[dict[str, Any]] = []
    geometry_paths: list[dict[str, Any]] = []
    geometry_objects: list[dict[str, Any]] = []
    for segment_index, (segment, points) in enumerate(sampled):
        is_analytic_arc = segment.get("geometry", {}).get("path_kind") == "arc3d"
        circular_points = (
            [points[0], points[len(points) // 2], points[-1]] if is_analytic_arc else _circular_projection(points)
        )
        display_points = circular_points or points
        model_paths.append({
            "id": str(segment.get("id", "model-segment")),
            "panel_id": motion_panel,
            "kind": "trajectory",
            "geometry": "circular-arc" if circular_points else "polyline",
            "points": [normalize(_project(point)) for point in display_points],
            "label": _short_segment_label(segment),
            "direction": "right",
            "fact_ids": inherited_fact_ids,
        })
        geometry = segment.get("geometry", {})
        center = _point3(geometry.get("center")) if isinstance(geometry, dict) else None
        radius = geometry.get("radius") if isinstance(geometry, dict) else None
        if is_analytic_arc and center is not None and isinstance(radius, (int, float)) and not isinstance(radius, bool):
            center_point = normalize(_project(center))
            arc_point = normalize(_project(points[len(points) // 2]))
            center_id = f"model-center-{segment_index + 1}"
            geometry_objects.append({
                "id": center_id,
                "panel_id": motion_panel,
                "kind": "point",
                "x": max(0.0, min(97.5, center_point["x"] - 1.25)),
                "y": max(0.0, min(97.5, center_point["y"] - 1.25)),
                "width": 2.5,
                "height": 2.5,
                "label": f"O{segment_index + 1}",
                "polarity": "none",
                "fact_ids": [],
            })
            geometry_paths.append({
                "id": f"model-radius-{segment_index + 1}",
                "panel_id": motion_panel,
                "kind": "dimension",
                "geometry": "line",
                "points": [center_point, arc_point],
                "label": f"r={float(radius):g}d",
                "direction": "none",
                "fact_ids": [],
            })
    compiled["paths"] = other_paths + geometry_paths + model_paths
    existing_object_ids = {str(item.get("id", "")) for item in compiled.get("objects", []) if isinstance(item, dict)}
    event_objects: list[dict[str, Any]] = []
    for event in timeline if isinstance(timeline, list) else []:
        label = _event_label(event) if isinstance(event, dict) else ""
        position = _point3(event.get("position")) if isinstance(event, dict) else None
        event_id = f"model-event-{event.get('id', '')}" if isinstance(event, dict) else ""
        if not label or position is None or not event_id or event_id in existing_object_ids:
            continue
        point = normalize(_project(position))
        event_objects.append({
            "id": event_id,
            "panel_id": motion_panel,
            "kind": "point",
            "x": max(0.0, min(97.0, point["x"] - 1.5)),
            "y": max(0.0, min(97.0, point["y"] - 1.5)),
            "width": 3.0,
            "height": 3.0,
            "label": label,
            "polarity": "none",
            "fact_ids": [],
        })
    compiled["objects"] = compiled.get("objects", []) + geometry_objects + event_objects
    if is_spatial_model:
        for annotation in compiled.get("annotations", []):
            if isinstance(annotation, dict) and any(
                token in str(annotation.get("text", "")) for token in ("投影", "空间", "三维")
            ):
                annotation["text"] = "x-y 投影；z 位移另计"
        for region in compiled.get("regions", []):
            if isinstance(region, dict) and region.get("panel_id") == motion_panel:
                label = str(region.get("label", ""))
                if "B(t)" in label and "E(t)" in label:
                    region["label"] = "交变 B(t)、E(t)（垂直 x-y 面）"

    for region, origin in zip(boundary_regions, boundary_origins):
        label = str(region.get("label") or region.get("field", {}).get("label") or "").strip()
        short_label = label.replace(" 板", "").replace("板", "").strip()
        if not short_label:
            continue
        target = next(
            (
                item
                for item in compiled.get("objects", [])
                if isinstance(item, dict)
                and item.get("kind") == "plate"
                and str(item.get("label", "")).replace(" 板", "").replace("板", "").strip() == short_label
            ),
            None,
        )
        if target is not None:
            target["y"] = max(0.0, min(100.0 - float(target.get("height", 1)), normalize(_project(origin))["y"]))

    start_event = (
        next(
            (
                item
                for item in timeline
                if isinstance(item, dict)
                and str(item.get("id", "")).endswith("-start")
                and _point3(item.get("position")) is not None
            ),
            None,
        )
        if isinstance(timeline, list)
        else None
    )
    if start_event is not None:
        start = normalize(_project(_point3(start_event["position"])))
        particle = next(
            (item for item in compiled.get("objects", []) if isinstance(item, dict) and item.get("kind") == "particle"),
            None,
        )
        if particle is not None:
            particle["x"] = max(0.0, min(100.0 - float(particle.get("width", 1)), start["x"]))
            particle["y"] = max(0.0, min(100.0 - float(particle.get("height", 1)), start["y"]))

    report = {
        "status": "compiled",
        "model_fingerprint": _fingerprint(physics_model),
        "trajectory_path_ids": [item["id"] for item in model_paths],
        "component_ids": ["parallel-plates", "particle-launch", "trajectory-segment", "event-marker"],
        "projection": "magnetic-top-view-x-right-y-up; z-explained-separately",
        "geometry_aid_ids": [item["id"] for item in geometry_paths + geometry_objects + event_objects],
    }
    report["fingerprint"] = _fingerprint(report)
    return compiled, report
