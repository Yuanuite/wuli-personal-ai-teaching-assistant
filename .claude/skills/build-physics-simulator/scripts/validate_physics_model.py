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
