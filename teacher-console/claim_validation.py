#!/usr/bin/env python3
"""Claim-level verification routing and deterministic certificate checks."""

from __future__ import annotations

import ast
import json
import operator
import re
from fractions import Fraction
from typing import Any

import claim_ledger
import correctness_policy

VALIDATION_POLICY = "wuli.claim-validation.v1"
RISK_LEVELS = ("low", "medium", "high", "critical")

_DETERMINISTIC_CHECK_TYPES = {
    "arithmetic",
    "dimension",
    "interval",
    "event-order",
    "aggregation",
}
_VARIABLE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_DIMENSION_BASES = ("M", "L", "T", "I", "Theta", "N", "J")
_KNOWN_UNIT_DIMENSIONS: dict[str, dict[str, int]] = {
    "dimensionless": {},
    "kg": {"M": 1},
    "m": {"L": 1},
    "s": {"T": 1},
    "ampere": {"I": 1},
    "coulomb": {"I": 1, "T": 1},
    "newton": {"M": 1, "L": 1, "T": -2},
    "joule": {"M": 1, "L": 2, "T": -2},
    "watt": {"M": 1, "L": 2, "T": -3},
    "volt": {"M": 1, "L": 2, "T": -3, "I": -1},
    "ohm": {"M": 1, "L": 2, "T": -3, "I": -2},
    "tesla": {"M": 1, "T": -2, "I": -1},
    "weber": {"M": 1, "L": 2, "T": -2, "I": -1},
}


def _route(verifier_kind: str, check_type: str) -> dict[str, Any]:
    return {
        "verifier_kind": verifier_kind,
        "check_type": check_type,
        "context_isolated": verifier_kind == "independent-agent",
    }


def _base_routes(claim: dict[str, Any]) -> list[dict[str, Any]]:
    kind = claim["kind"]
    check_spec = claim["check_spec"]
    requested_type = str(check_spec.get("type", "")).strip().lower() if isinstance(check_spec, dict) else ""
    if requested_type == "semantic-required":
        requested_type = "semantic"
    if requested_type and requested_type not in correctness_policy.CHECK_TYPES:
        raise ValueError(f"unsupported claim check_spec type: {requested_type!r}")

    if kind == "premise":
        return [
            _route("source", "source-match"),
            _route("teacher", "source-match"),
        ]
    if kind == "model":
        return [
            _route("independent-agent", "semantic"),
            _route("teacher", "semantic"),
        ]

    preferred = requested_type
    if not preferred:
        preferred = {
            "derived": "semantic",
            "numerical": "arithmetic",
            "boundary": "event-order",
            "final": "aggregation",
        }[kind]
    requirement = correctness_policy.certificate_requirement(kind)
    if preferred not in requirement["check_types"]:
        # A structurally valid check may still be insufficient for this Claim
        # kind.  Reject it instead of silently verifying under a weaker policy.
        raise ValueError(f"{preferred} is not an accepted check type for claim kind {kind}")
    routes: list[dict[str, Any]] = []
    if preferred in _DETERMINISTIC_CHECK_TYPES:
        routes.append(_route("deterministic", preferred))
    routes.extend([
        _route("independent-agent", "semantic"),
        _route("teacher", "semantic"),
    ])
    return routes


def route_claim_verification(
    claim: dict[str, Any],
    *,
    risk: str = "medium",
) -> dict[str, Any]:
    """Return explicit certificate groups; every group must later be satisfied."""
    normalized = claim_ledger.normalize_claim(claim, agent_submission=False)
    risk_level = str(risk).strip().lower()
    if risk_level not in RISK_LEVELS:
        raise ValueError(f"unknown claim risk: {risk!r}")
    groups = [
        {
            "id": "base",
            "one_of": _base_routes(normalized),
            "distinct_verifier_required": False,
        }
    ]
    if risk_level in {"high", "critical"} and normalized["kind"] != "premise":
        groups.append({
            "id": "independent-confirmation",
            "one_of": [
                _route("independent-agent", "semantic"),
                _route("teacher", "semantic"),
            ],
            "distinct_verifier_required": True,
        })
    return {
        "policy": VALIDATION_POLICY,
        "claim_id": normalized["id"],
        "claim_version": normalized["version"],
        "claim_kind": normalized["kind"],
        "risk": risk_level,
        "required_groups": groups,
    }


def certificate_matches_route(
    certificate: dict[str, Any],
    route: dict[str, Any],
) -> bool:
    """Check route shape only; verdict and Claim binding are handled at promotion."""
    normalized = claim_ledger.normalize_certificate(certificate)
    if normalized["verifier_kind"] != route.get("verifier_kind"):
        return False
    if normalized["check_type"] != route.get("check_type"):
        return False
    if route.get("context_isolated") and not normalized["verifier_identity"]["context_isolated"]:
        return False
    return True


def _fraction(value: Any, field: str) -> Fraction:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite rational value")
    try:
        if isinstance(value, int):
            result = Fraction(value)
        elif isinstance(value, float):
            result = Fraction(str(value))
        elif isinstance(value, str) and len(value.strip()) <= 80:
            result = Fraction(value.strip())
        else:
            raise ValueError
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{field} must be a finite rational value") from exc
    _bounded_fraction(result)
    return result


def _bounded_fraction(value: Fraction) -> Fraction:
    if value.numerator.bit_length() > 4096 or value.denominator.bit_length() > 4096:
        raise ValueError("arithmetic result exceeds safety limit")
    return value


def safe_evaluate_arithmetic(
    expression: str,
    variables: dict[str, Any] | None = None,
) -> Fraction:
    """Evaluate a small arithmetic AST without executing Python code."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("arithmetic expression must be a non-empty string")
    if len(expression) > 240:
        raise ValueError("arithmetic expression exceeds 240 characters")
    raw_variables = variables or {}
    if not isinstance(raw_variables, dict) or len(raw_variables) > 32:
        raise ValueError("arithmetic variables must be an object with at most 32 items")
    normalized_variables: dict[str, Fraction] = {}
    for name, value in raw_variables.items():
        if not isinstance(name, str) or not _VARIABLE_PATTERN.fullmatch(name):
            raise ValueError("arithmetic variable name is invalid")
        normalized_variables[name] = _fraction(value, f"variable {name}")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("arithmetic expression syntax is invalid") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 64:
        raise ValueError("arithmetic expression is too complex")

    def evaluate(node: ast.AST) -> Fraction:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("arithmetic constants must be real numbers")
            return _fraction(node.value, "arithmetic constant")
        if isinstance(node, ast.Name):
            if node.id not in normalized_variables:
                raise ValueError(f"arithmetic variable is unbound: {node.id}")
            return normalized_variables[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Pow):
                if right.denominator != 1 or abs(right.numerator) > 12:
                    raise ValueError("arithmetic exponent must be an integer from -12 to 12")
                if left == 0 and right.numerator < 0:
                    raise ValueError("division by zero")
                return _bounded_fraction(left**right.numerator)
            operation = _BINARY_OPERATORS.get(type(node.op))
            if operation is None:
                raise ValueError("arithmetic operator is not allowed")
            if isinstance(node.op, ast.Div) and right == 0:
                raise ValueError("division by zero")
            return _bounded_fraction(operation(left, right))
        raise ValueError(f"arithmetic syntax is not allowed: {type(node).__name__}")

    return _bounded_fraction(evaluate(tree))


def _relation_passes(
    left: Fraction,
    relation: str,
    right: Fraction,
    tolerance: Fraction,
) -> bool:
    difference = left - right
    return {
        "==": abs(difference) <= tolerance,
        "!=": abs(difference) > tolerance,
        "<": left < right - tolerance,
        "<=": left <= right + tolerance,
        ">": left > right + tolerance,
        ">=": left >= right - tolerance,
    }[relation]


def verify_arithmetic_claim(
    claim: dict[str, Any],
    dependency_claims: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a normalized deterministic certificate for one arithmetic spec."""
    normalized = claim_ledger.normalize_claim(claim, agent_submission=False)
    input_fingerprint = claim_ledger.claim_verification_input_fingerprint(normalized, dependency_claims)
    spec = normalized["check_spec"]
    if not isinstance(spec, dict) or spec.get("type") != "arithmetic":
        raise ValueError("arithmetic verifier requires check_spec.type=arithmetic")
    unknown_spec_fields = set(spec) - {"type", "variables", "relations"}
    if unknown_spec_fields:
        raise ValueError(f"arithmetic check_spec has unknown fields: {sorted(unknown_spec_fields)}")
    variables = spec.get("variables", {})
    relations = spec.get("relations")
    if not isinstance(relations, list) or not relations or len(relations) > 12:
        raise ValueError("arithmetic relations must contain 1-12 items")

    checked: list[dict[str, str | bool]] = []
    decisive_checks: list[str] = []
    verdict = "pass"
    try:
        for index, relation_spec in enumerate(relations):
            if not isinstance(relation_spec, dict):
                raise ValueError("arithmetic relation must be an object")
            required = {"left", "operator", "right"}
            allowed = required | {"absolute_tolerance"}
            if set(relation_spec) - allowed or not required.issubset(relation_spec):
                raise ValueError("arithmetic relation fields are invalid")
            relation = str(relation_spec["operator"]).strip()
            if relation not in {"==", "!=", "<", "<=", ">", ">="}:
                raise ValueError("arithmetic relation operator is invalid")
            left = safe_evaluate_arithmetic(str(relation_spec["left"]), variables)
            right = safe_evaluate_arithmetic(str(relation_spec["right"]), variables)
            tolerance = _fraction(
                relation_spec.get("absolute_tolerance", "0"),
                "absolute_tolerance",
            )
            if tolerance < 0:
                raise ValueError("absolute_tolerance must not be negative")
            passed = _relation_passes(left, relation, right, tolerance)
            checked.append({
                "index": str(index + 1),
                "left": str(left),
                "operator": relation,
                "right": str(right),
                "tolerance": str(tolerance),
                "passed": passed,
            })
            decisive_checks.append(f"check {index + 1}: {left} {relation} {right} -> {passed}")
            if not passed:
                verdict = "conflict"
    except ValueError as exc:
        verdict = "unsupported"
        checked = [{"error": str(exc), "passed": False}]
        decisive_checks = []

    return claim_ledger.normalize_certificate({
        "claim_id": normalized["id"],
        "claim_version": normalized["version"],
        "verifier_kind": "deterministic",
        "check_type": "arithmetic",
        "verdict": verdict,
        "normalized_result": json.dumps(
            checked,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "decisive_checks": decisive_checks,
        "input_fingerprint": input_fingerprint,
        "verifier_identity": {
            "model_id": "safe-arithmetic-v1",
            "provider": "local",
            "context_isolated": True,
        },
    })


def _dimension_vector(
    raw: Any,
    field: str,
) -> dict[str, Fraction]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be a dimension object")
    unknown = set(raw) - set(_DIMENSION_BASES)
    if unknown:
        raise ValueError(f"{field} has unknown base dimensions: {sorted(unknown)}")
    result: dict[str, Fraction] = {}
    for base, exponent in raw.items():
        normalized = _fraction(exponent, f"{field}.{base}")
        if abs(normalized) > 16:
            raise ValueError(f"{field}.{base} exponent exceeds safety limit")
        if normalized:
            result[base] = normalized
    return result


def _combine_dimensions(
    left: dict[str, Fraction],
    right: dict[str, Fraction],
    sign: int,
) -> dict[str, Fraction]:
    result = dict(left)
    for base in _DIMENSION_BASES:
        exponent = result.get(base, Fraction(0)) + sign * right.get(base, Fraction(0))
        if abs(exponent) > 32:
            raise ValueError("dimension exponent exceeds safety limit")
        if exponent:
            result[base] = exponent
        else:
            result.pop(base, None)
    return result


def evaluate_dimension(
    expression: Any,
    symbols: dict[str, Any] | None = None,
    *,
    _depth: int = 0,
) -> dict[str, Fraction]:
    """Evaluate a bounded JSON dimension expression."""
    if _depth > 16:
        raise ValueError("dimension expression exceeds nesting limit")
    if not isinstance(expression, dict):
        raise ValueError("dimension expression must be an object")
    raw_symbols = symbols or {}
    if not isinstance(raw_symbols, dict) or len(raw_symbols) > 48:
        raise ValueError("dimension symbols must be an object with at most 48 items")

    if set(expression) == {"symbol"}:
        name = str(expression["symbol"]).strip()
        if not _VARIABLE_PATTERN.fullmatch(name):
            raise ValueError("dimension symbol name is invalid")
        if name not in raw_symbols:
            raise ValueError(f"unknown dimension symbol: {name}")
        return _dimension_vector(raw_symbols[name], f"dimension symbol {name}")
    if set(expression) == {"unit"}:
        unit = str(expression["unit"]).strip().lower()
        if unit not in _KNOWN_UNIT_DIMENSIONS:
            raise ValueError(f"unknown dimension unit: {unit}")
        return _dimension_vector(_KNOWN_UNIT_DIMENSIONS[unit], f"unit {unit}")
    if set(expression) == {"dimension"}:
        return _dimension_vector(expression["dimension"], "dimension")

    operation_name = expression.get("op")
    if operation_name == "mul" and set(expression) == {"op", "args"}:
        arguments = expression["args"]
        if not isinstance(arguments, list) or not (2 <= len(arguments) <= 12):
            raise ValueError("dimension mul requires 2-12 arguments")
        result: dict[str, Fraction] = {}
        for argument in arguments:
            result = _combine_dimensions(
                result,
                evaluate_dimension(argument, raw_symbols, _depth=_depth + 1),
                1,
            )
        return result
    if operation_name == "div" and set(expression) == {"op", "left", "right"}:
        return _combine_dimensions(
            evaluate_dimension(expression["left"], raw_symbols, _depth=_depth + 1),
            evaluate_dimension(expression["right"], raw_symbols, _depth=_depth + 1),
            -1,
        )
    if operation_name == "pow" and set(expression) == {"op", "base", "exponent"}:
        exponent = _fraction(expression["exponent"], "dimension exponent")
        if abs(exponent) > 12:
            raise ValueError("dimension exponent exceeds safety limit")
        base = evaluate_dimension(expression["base"], raw_symbols, _depth=_depth + 1)
        return {key: value * exponent for key, value in base.items() if value * exponent}
    raise ValueError("dimension expression shape or operation is unsupported")


def _serialized_dimension(value: dict[str, Fraction]) -> dict[str, str]:
    return {base: str(value[base]) for base in _DIMENSION_BASES if base in value}


def verify_dimension_claim(
    claim: dict[str, Any],
    dependency_claims: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = claim_ledger.normalize_claim(claim, agent_submission=False)
    input_fingerprint = claim_ledger.claim_verification_input_fingerprint(normalized, dependency_claims)
    spec = normalized["check_spec"]
    if not isinstance(spec, dict) or spec.get("type") != "dimension":
        raise ValueError("dimension verifier requires check_spec.type=dimension")
    if set(spec) - {"type", "symbols", "relations"}:
        raise ValueError("dimension check_spec has unknown fields")
    symbols = spec.get("symbols", {})
    relations = spec.get("relations")
    if not isinstance(relations, list) or not relations or len(relations) > 12:
        raise ValueError("dimension relations must contain 1-12 items")

    checked: list[dict[str, Any]] = []
    decisive_checks: list[str] = []
    verdict = "pass"
    try:
        for index, relation in enumerate(relations):
            if not isinstance(relation, dict):
                raise ValueError("dimension relation must be an object")
            if set(relation) - {"left", "right", "label"} or not {
                "left",
                "right",
            }.issubset(relation):
                raise ValueError("dimension relation fields are invalid")
            left = evaluate_dimension(relation["left"], symbols)
            right = evaluate_dimension(relation["right"], symbols)
            passed = left == right
            label = str(relation.get("label", f"check {index + 1}")).strip()[:120]
            checked.append({
                "label": label,
                "left": _serialized_dimension(left),
                "right": _serialized_dimension(right),
                "passed": passed,
            })
            decisive_checks.append(
                f"{label}: {_serialized_dimension(left)} == {_serialized_dimension(right)} -> {passed}"
            )
            if not passed:
                verdict = "conflict"
    except ValueError as exc:
        verdict = "unsupported"
        checked = [{"error": str(exc), "passed": False}]
        decisive_checks = []

    return claim_ledger.normalize_certificate({
        "claim_id": normalized["id"],
        "claim_version": normalized["version"],
        "verifier_kind": "deterministic",
        "check_type": "dimension",
        "verdict": verdict,
        "normalized_result": json.dumps(
            checked,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "decisive_checks": decisive_checks,
        "input_fingerprint": input_fingerprint,
        "verifier_identity": {
            "model_id": "dimension-vector-v1",
            "provider": "local",
            "context_isolated": True,
        },
    })


def _endpoint(value: Any, side: str) -> tuple[int, Fraction | None]:
    if value is None:
        return (-1, None) if side == "lower" else (1, None)
    return (0, _fraction(value, f"interval {side} endpoint"))


def _compare_endpoints(
    left: tuple[int, Fraction | None],
    right: tuple[int, Fraction | None],
) -> int:
    if left[0] != right[0]:
        return -1 if left[0] < right[0] else 1
    if left[0] != 0:
        return 0
    assert left[1] is not None and right[1] is not None
    return (left[1] > right[1]) - (left[1] < right[1])


def _endpoint_text(value: tuple[int, Fraction | None]) -> str:
    if value[0] < 0:
        return "-inf"
    if value[0] > 0:
        return "+inf"
    return str(value[1])


def _normalize_interval(raw: Any, field: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be an object")
    required = {"lower", "upper", "lower_closed", "upper_closed"}
    if set(raw) - (required | {"label"}) or not required.issubset(raw):
        raise ValueError(f"{field} fields are invalid")
    if not isinstance(raw["lower_closed"], bool) or not isinstance(raw["upper_closed"], bool):
        raise ValueError(f"{field} closure flags must be boolean")
    lower = _endpoint(raw["lower"], "lower")
    upper = _endpoint(raw["upper"], "upper")
    if lower[0] < 0 and raw["lower_closed"]:
        raise ValueError(f"{field} cannot close negative infinity")
    if upper[0] > 0 and raw["upper_closed"]:
        raise ValueError(f"{field} cannot close positive infinity")
    comparison = _compare_endpoints(lower, upper)
    if comparison > 0 or (comparison == 0 and not (raw["lower_closed"] and raw["upper_closed"])):
        raise ValueError(f"{field} is empty or reversed")
    return {
        "lower": lower,
        "upper": upper,
        "lower_closed": raw["lower_closed"],
        "upper_closed": raw["upper_closed"],
        "label": str(raw.get("label", field)).strip()[:120],
    }


def _interval_report(spec: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str]]:
    if set(spec) - {"type", "domain", "segments"}:
        raise ValueError("interval check_spec has unknown fields")
    domain = _normalize_interval(spec.get("domain"), "interval domain")
    raw_segments = spec.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments or len(raw_segments) > 24:
        raise ValueError("interval segments must contain 1-24 items")
    segments = [_normalize_interval(item, f"interval segment {index + 1}") for index, item in enumerate(raw_segments)]
    segments.sort(
        key=lambda item: (
            item["lower"][0],
            item["lower"][1] if item["lower"][1] is not None else Fraction(0),
            not item["lower_closed"],
        )
    )
    issues: list[str] = []
    checks: list[dict[str, Any]] = []

    first = segments[0]
    if _compare_endpoints(first["lower"], domain["lower"]) != 0 or first["lower_closed"] != domain["lower_closed"]:
        issues.append("segments do not start at the exact domain boundary")
    last = segments[-1]
    if _compare_endpoints(last["upper"], domain["upper"]) != 0 or last["upper_closed"] != domain["upper_closed"]:
        issues.append("segments do not end at the exact domain boundary")

    for segment in segments:
        if _compare_endpoints(segment["lower"], domain["lower"]) < 0:
            issues.append(f"{segment['label']} starts outside the domain")
        if _compare_endpoints(segment["upper"], domain["upper"]) > 0:
            issues.append(f"{segment['label']} ends outside the domain")

    for previous, current in zip(segments, segments[1:]):
        comparison = _compare_endpoints(previous["upper"], current["lower"])
        boundary = (
            f"{previous['label']} -> {current['label']} at "
            f"{_endpoint_text(previous['upper'])}/"
            f"{_endpoint_text(current['lower'])}"
        )
        if comparison < 0:
            outcome = "gap"
            issues.append(f"gap between {previous['label']} and {current['label']}")
        elif comparison > 0:
            outcome = "overlap"
            issues.append(f"overlap between {previous['label']} and {current['label']}")
        else:
            ownership = int(previous["upper_closed"]) + int(current["lower_closed"])
            outcome = {0: "gap", 1: "exact", 2: "overlap"}[ownership]
            if outcome != "exact":
                issues.append(f"{outcome} at shared boundary of {previous['label']} and {current['label']}")
        checks.append({"boundary": boundary, "outcome": outcome})
    return ("conflict" if issues else "pass"), checks, issues


def _deterministic_certificate(
    claim: dict[str, Any],
    dependency_claims: list[dict[str, Any]],
    *,
    check_type: str,
    verifier_id: str,
    verdict: str,
    result: Any,
    decisive_checks: list[str],
) -> dict[str, Any]:
    normalized = claim_ledger.normalize_claim(claim, agent_submission=False)
    return claim_ledger.normalize_certificate({
        "claim_id": normalized["id"],
        "claim_version": normalized["version"],
        "verifier_kind": "deterministic",
        "check_type": check_type,
        "verdict": verdict,
        "normalized_result": json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "decisive_checks": decisive_checks,
        "input_fingerprint": claim_ledger.claim_verification_input_fingerprint(normalized, dependency_claims),
        "verifier_identity": {
            "model_id": verifier_id,
            "provider": "local",
            "context_isolated": True,
        },
    })


def verify_interval_claim(
    claim: dict[str, Any],
    dependency_claims: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = claim_ledger.normalize_claim(claim, agent_submission=False)
    spec = normalized["check_spec"]
    if not isinstance(spec, dict) or spec.get("type") != "interval":
        raise ValueError("interval verifier requires check_spec.type=interval")
    try:
        verdict, checks, issues = _interval_report(spec)
        decisive = [f"{item['boundary']} -> {item['outcome']}" for item in checks]
        if verdict == "pass" and not decisive:
            decisive = ["single segment exactly matches the declared domain"]
        result: Any = {"checks": checks, "issues": issues}
    except ValueError as exc:
        verdict = "unsupported"
        decisive = []
        result = {"error": str(exc)}
    return _deterministic_certificate(
        normalized,
        dependency_claims,
        check_type="interval",
        verifier_id="interval-partition-v1",
        verdict=verdict,
        result=result,
        decisive_checks=decisive,
    )


def _event_order_report(
    spec: dict[str, Any],
) -> tuple[str, dict[str, Any], list[str]]:
    allowed = {
        "type",
        "events",
        "selected_event_ids",
        "selection",
        "time_lower_bound",
        "time_upper_bound",
    }
    if set(spec) - allowed:
        raise ValueError("event-order check_spec has unknown fields")
    events = spec.get("events")
    selected = spec.get("selected_event_ids")
    selection = str(spec.get("selection", "")).strip().lower()
    if selection not in {"first", "last", "unique", "all"}:
        raise ValueError("event-order selection is invalid")
    if not isinstance(events, list) or not events or len(events) > 48:
        raise ValueError("event-order events must contain 1-48 items")
    if not isinstance(selected, list) or not selected:
        raise ValueError("event-order selected_event_ids must not be empty")
    if len(selected) != len(set(selected)):
        raise ValueError("event-order selected_event_ids contains duplicates")

    normalized_events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict) or set(event) != {
            "id",
            "time",
            "admissible",
        }:
            raise ValueError("event-order event fields are invalid")
        event_id = str(event["id"]).strip()
        if not _VARIABLE_PATTERN.fullmatch(event_id) or event_id in seen_ids:
            raise ValueError("event-order event id is invalid or duplicated")
        if not isinstance(event["admissible"], bool):
            raise ValueError("event-order admissible must be boolean")
        seen_ids.add(event_id)
        normalized_events.append({
            "id": event_id,
            "time": _fraction(event["time"], f"event {index + 1} time"),
            "admissible": event["admissible"],
        })
    if not set(selected).issubset(seen_ids):
        raise ValueError("event-order selected_event_ids contains unknown ids")

    lower_spec = spec.get("time_lower_bound")
    upper_spec = spec.get("time_upper_bound")

    def bound(raw: Any, name: str) -> tuple[Fraction, bool] | None:
        if raw is None:
            return None
        if not isinstance(raw, dict) or set(raw) != {"value", "inclusive"}:
            raise ValueError(f"{name} fields are invalid")
        if not isinstance(raw["inclusive"], bool):
            raise ValueError(f"{name}.inclusive must be boolean")
        return _fraction(raw["value"], f"{name}.value"), raw["inclusive"]

    lower = bound(lower_spec, "time_lower_bound")
    upper = bound(upper_spec, "time_upper_bound")

    def in_bounds(time: Fraction) -> bool:
        if lower is not None:
            if time < lower[0] or (time == lower[0] and not lower[1]):
                return False
        if upper is not None:
            if time > upper[0] or (time == upper[0] and not upper[1]):
                return False
        return True

    eligible = [event for event in normalized_events if event["admissible"] and in_bounds(event["time"])]
    if not eligible:
        return "conflict", {"eligible_event_ids": []}, ["no admissible event remains inside the declared time bounds"]
    if selection == "first":
        extreme = min(event["time"] for event in eligible)
        expected = {event["id"] for event in eligible if event["time"] == extreme}
    elif selection == "last":
        extreme = max(event["time"] for event in eligible)
        expected = {event["id"] for event in eligible if event["time"] == extreme}
    elif selection == "unique":
        expected = {eligible[0]["id"]} if len(eligible) == 1 else set()
    else:
        expected = {event["id"] for event in eligible}
    actual = set(selected)
    issues = []
    if selection == "unique" and len(eligible) != 1:
        issues.append(f"unique selection has {len(eligible)} admissible events")
    if actual != expected:
        issues.append(f"selected events {sorted(actual)} do not match expected {sorted(expected)}")
    report = {
        "selection": selection,
        "eligible_events": [{"id": item["id"], "time": str(item["time"])} for item in eligible],
        "selected_event_ids": sorted(actual),
        "expected_event_ids": sorted(expected),
    }
    return ("conflict" if issues else "pass"), report, issues


def verify_event_order_claim(
    claim: dict[str, Any],
    dependency_claims: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized = claim_ledger.normalize_claim(claim, agent_submission=False)
    spec = normalized["check_spec"]
    if not isinstance(spec, dict) or spec.get("type") != "event-order":
        raise ValueError("event-order verifier requires check_spec.type=event-order")
    try:
        verdict, report, issues = _event_order_report(spec)
        decisive = [
            (
                f"{report.get('selection')} selected "
                f"{report.get('selected_event_ids')} expected "
                f"{report.get('expected_event_ids')}"
            )
        ]
        result: Any = {**report, "issues": issues}
    except ValueError as exc:
        verdict = "unsupported"
        decisive = []
        result = {"error": str(exc)}
    return _deterministic_certificate(
        normalized,
        dependency_claims,
        check_type="event-order",
        verifier_id="event-order-v1",
        verdict=verdict,
        result=result,
        decisive_checks=decisive,
    )


def _verifier_identity_key(certificate: dict[str, Any]) -> tuple[str, str, str]:
    identity = certificate["verifier_identity"]
    return (
        certificate["verifier_kind"],
        identity["model_id"],
        identity["provider"],
    )


def _generator_matches(
    certificate: dict[str, Any],
    generator_identity: dict[str, str] | None,
) -> bool:
    if not generator_identity or certificate["verifier_kind"] != "independent-agent":
        return False
    identity = certificate["verifier_identity"]
    return (
        identity["model_id"] == str(generator_identity.get("model_id", "")).strip()
        and identity["provider"] == str(generator_identity.get("provider", "")).strip()
    )


def _select_group_certificates(
    groups: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
) -> list[int] | None:
    candidates_by_group: list[list[int]] = []
    for group in groups:
        candidates_by_group.append([
            index
            for index, certificate in enumerate(certificates)
            if certificate["verdict"] == "pass"
            and any(certificate_matches_route(certificate, route) for route in group["one_of"])
        ])

    def search(
        group_index: int,
        selected_indexes: list[int],
        selected_identities: set[tuple[str, str, str]],
    ) -> list[int] | None:
        if group_index == len(groups):
            return selected_indexes
        group = groups[group_index]
        for certificate_index in candidates_by_group[group_index]:
            if certificate_index in selected_indexes:
                continue
            identity = _verifier_identity_key(certificates[certificate_index])
            if group["distinct_verifier_required"] and identity in selected_identities:
                continue
            result = search(
                group_index + 1,
                [*selected_indexes, certificate_index],
                {*selected_identities, identity},
            )
            if result is not None:
                return result
        return None

    return search(0, [], set())


def assess_claim_certificates(
    claim: dict[str, Any],
    dependency_claims: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
    *,
    risk: str = "medium",
    generator_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Determine trust eligibility without mutating the Claim."""
    normalized_claim = claim_ledger.normalize_claim(claim, agent_submission=False)
    normalized_dependencies = [claim_ledger.normalize_claim(item, agent_submission=False) for item in dependency_claims]
    expected_fingerprint = claim_ledger.claim_verification_input_fingerprint(normalized_claim, normalized_dependencies)
    route = route_claim_verification(normalized_claim, risk=risk)
    all_certificates = [claim_ledger.normalize_certificate(item) for item in certificates]

    stale: list[dict[str, Any]] = []
    self_verified: list[dict[str, Any]] = []
    eligible_certificates: list[dict[str, Any]] = []
    accepted_routes = [item for group in route["required_groups"] for item in group["one_of"]]
    for certificate in all_certificates:
        if certificate["claim_id"] != normalized_claim["id"]:
            continue
        if (
            certificate["claim_version"] != normalized_claim["version"]
            or certificate["input_fingerprint"] != expected_fingerprint
        ):
            stale.append(certificate)
            continue
        if not any(certificate_matches_route(certificate, item) for item in accepted_routes):
            continue
        if _generator_matches(certificate, generator_identity):
            self_verified.append(certificate)
            if certificate["verdict"] == "pass":
                continue
        eligible_certificates.append(certificate)

    conflicts = [item for item in eligible_certificates if item["verdict"] == "conflict"]
    incomplete = [item for item in eligible_certificates if item["verdict"] in {"insufficient", "unsupported"}]
    unverified_dependencies = [item["id"] for item in normalized_dependencies if item["status"] != "verified"]
    selection = _select_group_certificates(route["required_groups"], eligible_certificates)
    issues: list[str] = []
    if stale:
        issues.append(f"{len(stale)} stale certificate(s) ignored")
    if self_verified:
        issues.append(f"{len(self_verified)} generator self-certificate(s) rejected")
    if unverified_dependencies:
        issues.append(f"dependencies are not verified: {sorted(unverified_dependencies)}")
    if conflicts:
        issues.append(f"{len(conflicts)} accepted-route conflict(s)")
    if incomplete:
        issues.append(f"{len(incomplete)} accepted-route insufficient/unsupported result(s)")
    if selection is None:
        issues.append("required certificate groups are incomplete")

    if conflicts:
        decision = "disputed"
    elif unverified_dependencies or selection is None:
        decision = "provisional"
    else:
        decision = "verified"
    return {
        "policy": VALIDATION_POLICY,
        "claim_id": normalized_claim["id"],
        "claim_version": normalized_claim["version"],
        "decision": decision,
        "expected_input_fingerprint": expected_fingerprint,
        "required_group_count": len(route["required_groups"]),
        "satisfied_group_count": 0 if selection is None else len(selection),
        "selected_certificate_indexes": selection or [],
        "stale_certificate_count": len(stale),
        "self_certificate_count": len(self_verified),
        "conflict_count": len(conflicts),
        "incomplete_result_count": len(incomplete),
        "unverified_dependency_ids": sorted(unverified_dependencies),
        "issues": issues,
    }


def apply_certificate_decision(
    claim: dict[str, Any],
    dependency_claims: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
    *,
    risk: str = "medium",
    generator_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a copied Claim plus assessment; missing evidence never promotes."""
    normalized = claim_ledger.normalize_claim(claim, agent_submission=False)
    assessment = assess_claim_certificates(
        normalized,
        dependency_claims,
        certificates,
        risk=risk,
        generator_identity=generator_identity,
    )
    target_status = normalized["status"]
    if assessment["decision"] == "verified":
        if target_status == "candidate":
            target_status = correctness_policy.require_claim_status_transition(target_status, "verified")
        elif target_status != "verified":
            raise ValueError("non-candidate disputed Claim cannot be re-verified in place")
    elif assessment["decision"] == "disputed":
        if target_status in {"candidate", "verified"}:
            target_status = correctness_policy.require_claim_status_transition(target_status, "disputed")
    # Provisional means keep the current status; lack of evidence is never
    # rewritten into evidence and can be satisfied by later certificates.
    return {
        "claim": {**normalized, "status": target_status},
        "assessment": assessment,
    }


def _bridge_claim_ids(claims: dict[str, dict[str, Any]]) -> set[str]:
    bridges: set[str] = set()
    for claim_id, claim in claims.items():
        claim_stages = set(claim["stage_ids"])
        dependency_stages = {
            stage_id for dependency_id in claim["depends_on"] for stage_id in claims[dependency_id]["stage_ids"]
        }
        if claim_stages and dependency_stages and claim_stages != dependency_stages:
            bridges.add(claim_id)
    return bridges


def evaluate_claim_graph_evidence(
    claims: list[dict[str, Any]],
    certificates: list[dict[str, Any]],
    *,
    expected_target_ids: set[str],
    expected_obligation_ids: set[str],
    risks: dict[str, str] | None = None,
    generator_identities: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Recompute whole-graph trust in dependency order and fail closed."""
    graph_report = claim_ledger.validate_claim_graph(
        claims,
        expected_target_ids=expected_target_ids,
        expected_obligation_ids=expected_obligation_ids,
    )
    original = claim_ledger.active_claims(claims)
    bridge_ids = _bridge_claim_ids(original)
    trusted: dict[str, dict[str, Any]] = {}
    assessments: dict[str, dict[str, Any]] = {}
    risk_by_claim = risks or {}
    generator_by_claim = generator_identities or {}

    for claim_id in graph_report["topological_order"]:
        current = original[claim_id]
        dependencies = [trusted[dependency_id] for dependency_id in current["depends_on"]]
        assessment = assess_claim_certificates(
            current,
            dependencies,
            certificates,
            risk=risk_by_claim.get(claim_id, "medium"),
            generator_identity=generator_by_claim.get(claim_id),
        )
        if assessment["decision"] == "verified":
            audit_status = "verified"
        elif assessment["decision"] == "disputed":
            audit_status = "disputed"
        else:
            audit_status = "candidate"
        trusted[claim_id] = {**current, "status": audit_status}
        assessments[claim_id] = assessment

    critical_ids = {
        claim_id for claim_id, claim in trusted.items() if claim["kind"] in {"final", "boundary"}
    } | bridge_ids
    verified_ids = {claim_id for claim_id, claim in trusted.items() if claim["status"] == "verified"}
    disputed_ids = {claim_id for claim_id, claim in trusted.items() if claim["status"] == "disputed"}
    verified_critical = critical_ids & verified_ids
    critical_coverage = len(verified_critical) / len(critical_ids) if critical_ids else 0.0
    all_claims_verified = len(verified_ids) == len(trusted)
    if disputed_ids:
        result_status = "UNRESOLVED"
    elif all_claims_verified and critical_coverage == 1.0:
        result_status = "VERIFIED"
    else:
        result_status = "PROVISIONAL"

    return {
        "policy": VALIDATION_POLICY,
        "result_status": result_status,
        "graph": graph_report,
        "claims": [trusted[claim_id] for claim_id in graph_report["topological_order"]],
        "assessments": [assessments[claim_id] for claim_id in graph_report["topological_order"]],
        "verified_claim_ids": sorted(verified_ids),
        "disputed_claim_ids": sorted(disputed_ids),
        "critical_claim_ids": sorted(critical_ids),
        "verified_critical_claim_ids": sorted(verified_critical),
        "critical_certificate_coverage": round(critical_coverage, 6),
        "all_claims_verified": all_claims_verified,
    }
