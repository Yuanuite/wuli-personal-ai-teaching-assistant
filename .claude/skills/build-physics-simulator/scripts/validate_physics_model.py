#!/usr/bin/env python3
"""Validate a physics-model.json event ledger and answer cases."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "references" / "physics-model.schema.json"


def validate_schema(data: dict) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema dependency is unavailable; structural validation was not run"]

    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        return [f"cannot load physics-model.schema.json: {exc}"]

    errors = []
    for error in sorted(Draft202012Validator(schema).iter_errors(data), key=lambda item: list(item.absolute_path)):
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"schema {location}: {error.message}")
    return errors


def near_p(angle: float) -> bool:
    wrapped = ((angle + 180.0) % 360.0) - 180.0
    return abs(wrapped) < 1e-6


def validate_legacy_event_model(data: dict, errors: list[str]) -> None:
    event_model = data.get("event_model", {})
    timeline = sorted(event_model.get("timeline", []), key=lambda item: item.get("order", 0))
    cases = event_model.get("cases", [])
    event_ids = [item.get("id") for item in timeline]
    stop_id = event_model.get("stop_event_id")
    stop_order = next((item.get("order", math.inf) for item in timeline if item.get("id") == stop_id), math.inf)
    for case in cases:
        case_id = case.get("id", "")
        delta = float(case.get("delta_deg", math.nan))
        if not 0 < delta < 180:
            errors.append(f"{case_id}: delta must satisfy 0<delta<180")
            continue
        crossings = []
        for event in timeline:
            if event.get("order", math.inf) >= stop_order or not event.get("p_candidate"):
                continue
            angle = float(event.get("constant_deg", 0)) + float(event.get("delta_coefficient", 0)) * delta
            if near_p(angle):
                crossings.append(event.get("id"))
        if not crossings:
            errors.append(f"{case_id}: no P crossing before stop event")
        elif case.get("first_p_event_id") != crossings[0]:
            errors.append(f"{case_id}: first_p_event_id={case.get('first_p_event_id')} but computed {crossings[0]}")
        expected_ratio = 1.0 / (4.0 * math.sqrt(3.0) * math.tan(math.radians(delta / 2.0)))
        if abs(float(case.get("b3_ratio", math.nan)) - expected_ratio) > 1e-8:
            errors.append(f"{case_id}: b3_ratio does not match delta")


def validate_opposite_circular(data: dict, errors: list[str]) -> None:
    cases = data.get("event_model", {}).get("cases", [])
    valid_cases = 0
    invalid_cases = 0
    for case in cases:
        case_id = str(case.get("id", ""))
        n = case.get("n")
        segments = case.get("segment_count")
        delta = float(case.get("delta_deg", math.nan))
        valid = bool(case.get("valid", True))
        if not isinstance(n, int) or n < 2:
            errors.append(f"{case_id}: n must be an integer >= 2")
            continue
        if segments != n + 1:
            errors.append(f"{case_id}: segment_count must equal n+1")
        if not 0 < delta < 180:
            errors.append(f"{case_id}: delta must satisfy 0<delta<180")
            continue
        expected_delta = 360.0 / (n + 1)
        if valid:
            valid_cases += 1
            if abs(delta - expected_delta) > 1e-8:
                errors.append(f"{case_id}: valid case delta must equal 360/(n+1)")
            expected_radius = math.tan(math.radians(delta / 2.0))
            if abs(float(case.get("orbit_radius_ratio", math.nan)) - expected_radius) > 1e-8:
                errors.append(f"{case_id}: orbit_radius_ratio does not match tan(delta/2)")
            inside = (segments + 1) // 2
            outside = segments // 2
            expected_angle = inside * (math.pi - math.radians(delta)) + outside * (math.pi + math.radians(delta))
            if abs(float(case.get("total_gyro_angle_rad", math.nan)) - expected_angle) > 1e-8:
                errors.append(f"{case_id}: total_gyro_angle_rad is inconsistent")
            if case.get("first_p_event_id") != "return-p":
                errors.append(f"{case_id}: valid case must first return at return-p")
        else:
            invalid_cases += 1
            if abs(delta - expected_delta) < 1e-8:
                errors.append(f"{case_id}: invalid teaching case unexpectedly closes")
    if valid_cases < 3:
        errors.append("opposite-circular-magnetic: at least three valid n presets are required")
    if invalid_cases < 1:
        errors.append("opposite-circular-magnetic: include one nearby non-solution counting case")


def validate_electric_magnetic(data: dict, errors: list[str]) -> None:
    facts = data.get("facts", {})
    expected = {
        "xq_over_h": 2.0 * math.sqrt(3.0) / 3.0,
        "vq_over_v0": 2.0,
        "theta_deg": 60.0,
        "tangent_radius_over_d": 2.0 / 3.0,
        "b_coefficient": 3.0,
        "magnetic_sweep_deg": 120.0,
    }
    for key, value in expected.items():
        if abs(float(facts.get(key, math.nan)) - value) > 1e-8:
            errors.append(f"electric-to-bounded-magnetic: facts.{key} is inconsistent")
    parameters = data.get("simulation", {}).get("parameters", {})
    b_ratio = parameters.get("b_ratio", {})
    if not (float(b_ratio.get("min", 1)) < 1 < float(b_ratio.get("max", 1))):
        errors.append("electric-to-bounded-magnetic: B/B* control must span both sides of 1")
    pause_ids = set(data.get("simulation", {}).get("pause_event_ids", []))
    if not {"enter-q", "lower-boundary"}.issubset(pause_ids):
        errors.append("electric-to-bounded-magnetic: Q and lower-boundary must be pause events")


def validate_planar_magnetic(data: dict, errors: list[str]) -> None:
    facts = data.get("facts", {})
    particles = facts.get("particles", [])
    if not isinstance(particles, list) or len(particles) < 1:
        errors.append("planar-magnetic-multi-particle: facts.particles must contain at least one particle")
    particle_ids = {str(item.get("id")) for item in particles if isinstance(item, dict) and item.get("id")}
    if len(particle_ids) != len(particles):
        errors.append("planar-magnetic-multi-particle: every particle needs a unique id")
    regions = data.get("regions", [])
    if len(regions) < 2:
        errors.append("planar-magnetic-multi-particle: at least two magnetic regions are required")
    if str(facts.get("boundary", {}).get("type", "")) != "horizontal-line":
        errors.append("planar-magnetic-multi-particle: facts.boundary.type must be horizontal-line")

    timeline = data.get("event_model", {}).get("timeline", [])
    event_ids = {event.get("id") for event in timeline}
    event_times = []
    for event in timeline:
        try:
            event_times.append(float(event.get("time_tau")))
        except (TypeError, ValueError):
            errors.append(f"{event.get('id', '<event>')}: timeline event needs numeric time_tau")
    if event_times != sorted(event_times):
        errors.append("planar-magnetic-multi-particle: timeline time_tau must be nondecreasing")

    segments = data.get("trajectory", {}).get("segments", [])
    if not segments:
        errors.append("planar-magnetic-multi-particle: trajectory.segments is required")
    for segment in segments:
        segment_id = segment.get("id", "<segment>")
        particle_id = str(segment.get("particle_id", ""))
        if particle_id not in particle_ids:
            errors.append(f"{segment_id}: particle_id is unknown")
        if segment.get("type") != "arc":
            errors.append(f"{segment_id}: only arc segments are supported")
        if segment.get("start_event") not in event_ids:
            errors.append(f"{segment_id}: start_event is not in timeline")
        if segment.get("end_event") not in event_ids:
            errors.append(f"{segment_id}: end_event is not in timeline")
        geometry = segment.get("geometry", {})
        center = geometry.get("center")
        if not (isinstance(center, list) and len(center) == 2 and all(isinstance(value, (int, float)) for value in center)):
            errors.append(f"{segment_id}: geometry.center must be [x, y]")
        for key in ("radius", "start_deg", "end_deg"):
            if not isinstance(geometry.get(key), (int, float)):
                errors.append(f"{segment_id}: geometry.{key} must be numeric")
        if isinstance(geometry.get("radius"), (int, float)) and float(geometry.get("radius")) <= 0:
            errors.append(f"{segment_id}: geometry.radius must be positive")
        duration = segment.get("kinematics", {}).get("duration_tau")
        if not isinstance(duration, (int, float)) or float(duration) <= 0:
            errors.append(f"{segment_id}: kinematics.duration_tau must be positive")

    pause_ids = set(data.get("simulation", {}).get("pause_event_ids", []))
    stop_id = data.get("event_model", {}).get("stop_event_id")
    if stop_id and stop_id not in pause_ids:
        errors.append("planar-magnetic-multi-particle: stop_event_id should be a pause event")


def validate_piecewise_particle_2d(data: dict, errors: list[str]) -> None:
    facts = data.get("facts", {})
    viewport = facts.get("viewport", {})
    try:
        xmin, xmax = float(viewport["xmin"]), float(viewport["xmax"])
        ymin, ymax = float(viewport["ymin"]), float(viewport["ymax"])
        if not xmin < xmax or not ymin < ymax:
            errors.append("piecewise-field-particle-2d: viewport bounds must increase")
    except (KeyError, TypeError, ValueError):
        errors.append("piecewise-field-particle-2d: facts.viewport needs numeric xmin/xmax/ymin/ymax")

    particles = facts.get("particles", [])
    if not isinstance(particles, list) or not particles:
        errors.append("piecewise-field-particle-2d: facts.particles must not be empty")
        particles = []
    particle_ids = {str(item.get("id")) for item in particles if isinstance(item, dict) and item.get("id")}
    if len(particle_ids) != len(particles):
        errors.append("piecewise-field-particle-2d: every particle needs a unique id")

    timeline = sorted(data.get("event_model", {}).get("timeline", []), key=lambda item: item.get("order", 0))
    event_ids = {str(event.get("id")) for event in timeline}
    event_times_by_case: dict[str, list[float]] = {}
    for event in timeline:
        try:
            event_time = float(event["time"])
            if event_time < 0:
                errors.append(f"{event.get('id', '<event>')}: event time must be nonnegative")
            event_cases = event.get("case_ids") or ["*"]
            for case_id in event_cases:
                event_times_by_case.setdefault(str(case_id), []).append(event_time)
        except (KeyError, TypeError, ValueError):
            errors.append(f"{event.get('id', '<event>')}: event needs numeric time")
        position = event.get("position")
        if position is not None and not (
            isinstance(position, list)
            and len(position) == 2
            and all(isinstance(value, (int, float)) and math.isfinite(value) for value in position)
        ):
            errors.append(f"{event.get('id', '<event>')}: position must be finite [x, y]")
    for case_id, event_times in event_times_by_case.items():
        if event_times != sorted(event_times):
            errors.append(f"piecewise-field-particle-2d: timeline times must be nondecreasing for case {case_id}")

    cases = data.get("event_model", {}).get("cases", [])
    case_ids = {str(case.get("id")) for case in cases if isinstance(case, dict) and case.get("id")}
    if len(case_ids) != len(cases):
        errors.append("piecewise-field-particle-2d: every case needs a unique id")

    region_ids = {str(region.get("id")) for region in data.get("regions", [])}
    supported_shapes = {"rect", "circle", "half-plane", "polygon"}
    for region in data.get("regions", []):
        shape = region.get("shape", {})
        if not isinstance(shape, dict) or shape.get("type") not in supported_shapes:
            errors.append(f"{region.get('id', '<region>')}: unsupported 2D region shape")

    supported_segments = {"line", "arc", "polyline"}
    segments = data.get("trajectory", {}).get("segments", [])
    if not segments:
        errors.append("piecewise-field-particle-2d: trajectory.segments is required")
    for segment in segments:
        segment_id = str(segment.get("id", "<segment>"))
        if str(segment.get("particle_id", "")) not in particle_ids:
            errors.append(f"{segment_id}: particle_id is unknown")
        if segment.get("type") not in supported_segments:
            errors.append(f"{segment_id}: unsupported segment type")
        if segment.get("region") not in region_ids:
            errors.append(f"{segment_id}: region is unknown")
        if segment.get("start_event") not in event_ids or segment.get("end_event") not in event_ids:
            errors.append(f"{segment_id}: start_event/end_event must reference timeline")
        segment_cases = segment.get("case_ids", [])
        if not isinstance(segment_cases, list) or not segment_cases or any(case not in case_ids for case in segment_cases):
            errors.append(f"{segment_id}: case_ids must reference at least one known case")
        kinematics = segment.get("kinematics", {})
        try:
            start_time = float(kinematics["start_time"])
            end_time = float(kinematics["end_time"])
            if start_time < 0 or end_time <= start_time:
                errors.append(f"{segment_id}: kinematics must satisfy 0 <= start_time < end_time")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{segment_id}: kinematics needs numeric start_time/end_time")

        geometry = segment.get("geometry", {})
        if segment.get("type") == "line":
            for key in ("start", "end"):
                point = geometry.get(key)
                if not (
                    isinstance(point, list)
                    and len(point) == 2
                    and all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)
                ):
                    errors.append(f"{segment_id}: line geometry.{key} must be finite [x, y]")
        elif segment.get("type") == "arc":
            center = geometry.get("center")
            if not (
                isinstance(center, list)
                and len(center) == 2
                and all(isinstance(value, (int, float)) and math.isfinite(value) for value in center)
            ):
                errors.append(f"{segment_id}: arc center must be finite [x, y]")
            for key in ("radius", "start_deg", "end_deg"):
                if not isinstance(geometry.get(key), (int, float)) or not math.isfinite(float(geometry.get(key))):
                    errors.append(f"{segment_id}: arc geometry.{key} must be finite")
            if isinstance(geometry.get("radius"), (int, float)) and float(geometry["radius"]) <= 0:
                errors.append(f"{segment_id}: arc radius must be positive")
        else:
            points = geometry.get("points", [])
            if not (
                isinstance(points, list)
                and len(points) >= 2
                and all(
                    isinstance(point, list)
                    and len(point) == 2
                    and all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)
                    for point in points
                )
            ):
                errors.append(f"{segment_id}: polyline points must contain at least two finite [x, y] points")

    pause_ids = set(data.get("simulation", {}).get("pause_event_ids", []))
    unknown_pauses = pause_ids - event_ids
    if unknown_pauses:
        errors.append(f"piecewise-field-particle-2d: unknown pause events: {sorted(unknown_pauses)}")
    stop_id = data.get("event_model", {}).get("stop_event_id")
    if stop_id and stop_id not in pause_ids:
        errors.append("piecewise-field-particle-2d: stop_event_id should be a pause event")


def validate_piecewise_particle_3d(data: dict, errors: list[str]) -> None:
    def vector3(value) -> bool:
        return (
            isinstance(value, list)
            and len(value) == 3
            and all(isinstance(item, (int, float)) and math.isfinite(float(item)) for item in value)
        )

    facts = data.get("facts", {})
    particles = facts.get("particles", [])
    if not isinstance(particles, list) or not particles:
        errors.append("piecewise-field-particle-3d: facts.particles must not be empty")
        particles = []
    particle_ids = {str(item.get("id")) for item in particles if isinstance(item, dict) and item.get("id")}
    if len(particle_ids) != len(particles):
        errors.append("piecewise-field-particle-3d: every particle needs a unique id")

    axes = facts.get("axes", {})
    if not vector3(axes.get("origin")) or not vector3(axes.get("lengths")):
        errors.append("piecewise-field-particle-3d: facts.axes needs finite origin and lengths vectors")
    elif any(float(value) <= 0 for value in axes["lengths"]):
        errors.append("piecewise-field-particle-3d: facts.axes.lengths must be positive")

    timeline = sorted(data.get("event_model", {}).get("timeline", []), key=lambda item: item.get("order", 0))
    event_ids = {str(event.get("id")) for event in timeline}
    cases = data.get("event_model", {}).get("cases", [])
    case_ids = {str(case.get("id")) for case in cases if isinstance(case, dict) and case.get("id")}
    if len(case_ids) != len(cases):
        errors.append("piecewise-field-particle-3d: every case needs a unique id")
    for case in cases:
        try:
            if float(case["duration"]) <= 0:
                errors.append(f"{case.get('id', '<case>')}: duration must be positive")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{case.get('id', '<case>')}: duration must be numeric")

    times_by_case: dict[str, list[float]] = {case_id: [] for case_id in case_ids}
    for event in timeline:
        event_id = event.get("id", "<event>")
        if not vector3(event.get("position")):
            errors.append(f"{event_id}: position must be finite [x, y, z]")
        raw_time = event.get("time")
        if isinstance(raw_time, dict):
            for case_id, value in raw_time.items():
                if case_id not in case_ids:
                    errors.append(f"{event_id}: time references unknown case {case_id}")
                    continue
                try:
                    time_value = float(value)
                    if time_value < 0:
                        errors.append(f"{event_id}: event time must be nonnegative")
                    times_by_case.setdefault(case_id, []).append(time_value)
                except (TypeError, ValueError):
                    errors.append(f"{event_id}: event time for {case_id} must be numeric")
        else:
            try:
                time_value = float(raw_time)
                if time_value < 0:
                    errors.append(f"{event_id}: event time must be nonnegative")
                for case_id in event.get("case_ids") or case_ids:
                    if case_id not in case_ids:
                        errors.append(f"{event_id}: case_ids references unknown case {case_id}")
                    else:
                        times_by_case.setdefault(case_id, []).append(time_value)
            except (TypeError, ValueError):
                errors.append(f"{event_id}: event needs numeric or per-case time")
    for case_id, values in times_by_case.items():
        if values != sorted(values):
            errors.append(f"piecewise-field-particle-3d: timeline times must be nondecreasing for case {case_id}")

    supported_shapes = {"box", "plane", "cylinder", "wireframe"}
    region_ids: set[str] = set()
    for region in data.get("regions", []):
        region_id = str(region.get("id", ""))
        if not region_id or region_id in region_ids:
            errors.append("piecewise-field-particle-3d: every region needs a unique id")
        region_ids.add(region_id)
        shape = region.get("shape", {})
        shape_type = shape.get("type")
        if shape_type not in supported_shapes:
            errors.append(f"{region_id or '<region>'}: unsupported 3D region shape")
        elif shape_type == "box" and (not vector3(shape.get("min")) or not vector3(shape.get("max"))):
            errors.append(f"{region_id}: box needs finite min/max vectors")
        elif shape_type == "plane" and not all(vector3(shape.get(key)) for key in ("origin", "u", "v")):
            errors.append(f"{region_id}: plane needs finite origin/u/v vectors")
        elif shape_type == "cylinder":
            if not vector3(shape.get("center")) or not vector3(shape.get("axis")):
                errors.append(f"{region_id}: cylinder needs finite center/axis vectors")
            for key in ("radius", "length"):
                if not isinstance(shape.get(key), (int, float)) or float(shape[key]) <= 0:
                    errors.append(f"{region_id}: cylinder {key} must be positive")
        elif shape_type == "wireframe":
            edges = shape.get("edges", [])
            if not isinstance(edges, list) or not edges or any(
                not isinstance(edge, list) or len(edge) != 2 or not all(vector3(point) for point in edge)
                for edge in edges
            ):
                errors.append(f"{region_id}: wireframe needs finite 3D edge pairs")
        field = region.get("field", {})
        if isinstance(field, dict) and field.get("direction") is not None and not vector3(field.get("direction")):
            errors.append(f"{region_id}: field.direction must be finite [x, y, z]")

    supported_paths = {"line", "quadratic", "arc3d", "helix", "points"}
    segments = data.get("trajectory", {}).get("segments", [])
    if not segments:
        errors.append("piecewise-field-particle-3d: trajectory.segments is required")
    for segment in segments:
        segment_id = str(segment.get("id", "<segment>"))
        if str(segment.get("particle_id", "")) not in particle_ids:
            errors.append(f"{segment_id}: particle_id is unknown")
        if segment.get("type") != "polyline":
            errors.append(f"{segment_id}: 3D renderer uses polyline segments with analytic geometry")
        if segment.get("region") not in region_ids:
            errors.append(f"{segment_id}: region is unknown")
        if segment.get("start_event") not in event_ids or segment.get("end_event") not in event_ids:
            errors.append(f"{segment_id}: start_event/end_event must reference timeline")
        segment_cases = segment.get("case_ids", [])
        if not isinstance(segment_cases, list) or not segment_cases or any(case not in case_ids for case in segment_cases):
            errors.append(f"{segment_id}: case_ids must reference at least one known case")
        try:
            start_time = float(segment["kinematics"]["start_time"])
            end_time = float(segment["kinematics"]["end_time"])
            if start_time < 0 or end_time <= start_time:
                errors.append(f"{segment_id}: kinematics must satisfy 0 <= start_time < end_time")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{segment_id}: kinematics needs numeric start_time/end_time")

        geometry = segment.get("geometry", {})
        kind = geometry.get("path_kind")
        if kind not in supported_paths:
            errors.append(f"{segment_id}: unsupported 3D path_kind")
        elif kind == "line" and not all(vector3(geometry.get(key)) for key in ("start", "end")):
            errors.append(f"{segment_id}: line needs finite start/end vectors")
        elif kind == "quadratic":
            if not all(vector3(geometry.get(key)) for key in ("start", "velocity", "acceleration")):
                errors.append(f"{segment_id}: quadratic needs finite start/velocity/acceleration vectors")
            if not isinstance(geometry.get("duration"), (int, float)) or float(geometry["duration"]) <= 0:
                errors.append(f"{segment_id}: quadratic duration must be positive")
        elif kind == "arc3d":
            if not all(vector3(geometry.get(key)) for key in ("center", "basis_u", "basis_v")):
                errors.append(f"{segment_id}: arc3d needs finite center/basis_u/basis_v vectors")
            for key in ("radius", "start_deg", "end_deg"):
                if not isinstance(geometry.get(key), (int, float)) or not math.isfinite(float(geometry[key])):
                    errors.append(f"{segment_id}: arc3d {key} must be finite")
            if isinstance(geometry.get("radius"), (int, float)) and float(geometry["radius"]) <= 0:
                errors.append(f"{segment_id}: arc3d radius must be positive")
        elif kind == "helix":
            if not all(vector3(geometry.get(key)) for key in ("center_start", "axis", "basis_u", "basis_v")):
                errors.append(f"{segment_id}: helix needs finite center_start/axis/basis vectors")
            for key in ("radius", "start_deg", "end_deg", "advance"):
                if not isinstance(geometry.get(key), (int, float)) or not math.isfinite(float(geometry[key])):
                    errors.append(f"{segment_id}: helix {key} must be finite")
            if isinstance(geometry.get("radius"), (int, float)) and float(geometry["radius"]) <= 0:
                errors.append(f"{segment_id}: helix radius must be positive")
        elif kind == "points":
            points = geometry.get("points", [])
            if not isinstance(points, list) or len(points) < 2 or not all(vector3(point) for point in points):
                errors.append(f"{segment_id}: points path needs at least two finite [x, y, z] points")

    pause_ids = set(data.get("simulation", {}).get("pause_event_ids", []))
    unknown_pauses = pause_ids - event_ids
    if unknown_pauses:
        errors.append(f"piecewise-field-particle-3d: unknown pause events: {sorted(unknown_pauses)}")
    stop_id = data.get("event_model", {}).get("stop_event_id")
    if stop_id and stop_id not in pause_ids:
        errors.append("piecewise-field-particle-3d: stop_event_id should be a pause event")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    args = parser.parse_args()
    data = json.loads(args.model.read_text(encoding="utf-8"))
    errors: list[str] = validate_schema(data)
    warnings: list[str] = []

    event_model = data.get("event_model", {})
    timeline = sorted(event_model.get("timeline", []), key=lambda item: item.get("order", 0))
    cases = event_model.get("cases", [])
    event_ids = [item.get("id") for item in timeline]
    if len(event_ids) != len(set(event_ids)):
        errors.append("duplicate timeline event id")
    stop_id = event_model.get("stop_event_id")
    if stop_id not in event_ids:
        errors.append("stop_event_id is not in timeline")
        stop_order = math.inf
    else:
        stop_order = next(item["order"] for item in timeline if item["id"] == stop_id)

    seen_case_ids: set[str] = set()
    for case in cases:
        case_id = case.get("id", "")
        if case_id in seen_case_ids:
            errors.append(f"duplicate case id: {case_id}")
        seen_case_ids.add(case_id)

    model_type = data.get("model_type")
    if model_type == "concentric-radial-multi-field":
        validate_legacy_event_model(data, errors)
    elif model_type == "opposite-circular-magnetic":
        validate_opposite_circular(data, errors)
    elif model_type == "electric-to-bounded-magnetic":
        validate_electric_magnetic(data, errors)
    elif model_type == "planar-magnetic-multi-particle":
        validate_planar_magnetic(data, errors)
    elif model_type == "piecewise-field-particle-2d":
        validate_piecewise_particle_2d(data, errors)
    elif model_type == "piecewise-field-particle-3d":
        validate_piecewise_particle_3d(data, errors)
    else:
        errors.append(f"unsupported model_type: {model_type}")

    formulae = [formula for step in data.get("student_solution", {}).get("main_steps", []) for formula in step.get("formulae", [])]
    if not formulae:
        warnings.append("student_solution has no formulae")
    if len(data.get("technique_ids", [])) != len(set(data.get("technique_ids", []))):
        errors.append("duplicate technique id")

    report = {"model": str(args.model.resolve()), "valid": not errors, "errors": errors, "warnings": warnings, "cases": len(cases), "events": len(timeline)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
