"""Validate deterministic SVG output and bind MiMo evidence to DeepSeek generation."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

from visual_facts import normalize_payload

SCHEMA = "wuli.svg-collaboration-provenance.v1"
MAX_ELEMENTS = 2000
ALLOWED_TAGS = {
    "svg",
    "g",
    "defs",
    "marker",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "path",
    "text",
    "tspan",
    "title",
    "pattern",
}
ALLOWED_ATTRS = {
    "xmlns",
    "width",
    "height",
    "viewBox",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "d",
    "points",
    "fill",
    "stroke",
    "stroke-width",
    "opacity",
    "transform",
    "text-anchor",
    "dominant-baseline",
    "font-size",
    "font-family",
    "font-weight",
    "id",
    "markerWidth",
    "markerHeight",
    "refX",
    "refY",
    "orient",
    "marker-start",
    "marker-mid",
    "marker-end",
    "aria-label",
    "aria-labelledby",
    "role",
    "class",
    "baseline-shift",
    "fill-opacity",
    "font-style",
    "patternUnits",
    "stroke-dasharray",
    "stroke-linecap",
    "stroke-linejoin",
}


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    suffix = value[7:]
    return len(suffix) == 64 and all(character in "0123456789abcdef" for character in suffix)


def _local_name(name: str) -> str:
    return name.split("}", 1)[1] if name.startswith("{") else name


def _namespace(name: str) -> str:
    return name.split("}", 1)[0][1:] if name.startswith("{") else ""


def _validate_local_url(value: str) -> None:
    for match in re.finditer(r"url\(\s*([^)]*?)\s*\)", value):
        target = match.group(1).strip()
        if not target.startswith("#") or not target[1:]:
            raise ValueError("unsafe url() in SVG")
        if not all(character.isalnum() or character in "-_" for character in target[1:]):
            raise ValueError("unsafe url() in SVG")


def validate_svg_safety(svg_text: str) -> dict:
    """Validate a deliberately small, offline-safe SVG subset."""
    if not isinstance(svg_text, str):
        raise ValueError("svg_text must be a string")
    upper = svg_text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ValueError("DOCTYPE/entity declarations are not allowed")
    stripped = svg_text.lstrip()
    if not stripped.startswith(("<?xml", "<svg")):
        raise ValueError("non-whitespace text before SVG document")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise ValueError(f"malformed XML: {exc}") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError("root element is not svg")
    if _namespace(root.tag) not in ("", "http://www.w3.org/2000/svg"):
        raise ValueError("root namespace not allowed")

    count = 0
    stack = [root]
    while stack:
        element = stack.pop()
        count += 1
        if count > MAX_ELEMENTS:
            raise ValueError(f"element count exceeds {MAX_ELEMENTS}")
        local = _local_name(element.tag)
        namespace = _namespace(element.tag)
        if local not in ALLOWED_TAGS:
            raise ValueError(f"tag not allowed: {local}")
        if namespace not in ("", "http://www.w3.org/2000/svg"):
            raise ValueError(f"namespace not allowed: {namespace}")
        for raw_name, raw_value in element.attrib.items():
            name = _local_name(raw_name)
            if _namespace(raw_name) not in ("", "http://www.w3.org/2000/svg"):
                raise ValueError(f"attribute namespace not allowed: {raw_name}")
            if name not in ALLOWED_ATTRS or name.lower().startswith("on"):
                raise ValueError(f"attribute not allowed: {name}")
            value = str(raw_value).lower()
            if any(token in value for token in ("javascript", "@import", "data:", "http://", "https://")):
                raise ValueError("unsafe content in SVG attribute")
            _validate_local_url(str(raw_value))
        stack.extend(list(element))
    return {"status": "passed", "element_count": count}


def validate_and_bind_svg(
    svg_text: str,
    visual_facts: dict,
    generation_trace: dict,
    expected_generation_identity: dict,
) -> dict:
    """Return immutable provenance only when facts, route identity and SVG all validate."""
    if not isinstance(visual_facts, dict) or "source_fingerprint" not in visual_facts:
        raise ValueError("visual_facts must contain source_fingerprint")
    normalized = normalize_payload(visual_facts, visual_facts["source_fingerprint"])
    if normalized != visual_facts:
        raise ValueError("visual_facts are not canonical")
    if not isinstance(generation_trace, dict) or set(generation_trace) != {
        "model_id",
        "provider",
        "generation_fingerprint",
    }:
        raise ValueError("invalid generation_trace fields")
    if not isinstance(expected_generation_identity, dict) or set(expected_generation_identity) != {
        "model_id",
        "provider",
    }:
        raise ValueError("invalid expected_generation_identity fields")
    if any(
        not isinstance(expected_generation_identity[field], str) or not expected_generation_identity[field].strip()
        for field in ("model_id", "provider")
    ):
        raise ValueError("generation identity values must be non-empty strings")
    actual_identity = {
        "model_id": generation_trace["model_id"],
        "provider": generation_trace["provider"],
    }
    if actual_identity != expected_generation_identity:
        raise ValueError("generation identity mismatch")
    generation_fingerprint = generation_trace["generation_fingerprint"]
    if not _is_sha256(generation_fingerprint):
        raise ValueError("invalid generation fingerprint")
    safety = validate_svg_safety(svg_text)
    return {
        "schema": SCHEMA,
        "visual_facts_fingerprint": normalized["fingerprint"],
        "generation_fingerprint": generation_fingerprint,
        "svg_fingerprint": _sha256(svg_text.encode("utf-8")),
        "model_identity": actual_identity,
        "safety": safety,
    }
