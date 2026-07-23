#!/usr/bin/env python3
"""Render a small educational SVG from a JSON scene specification."""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
from typing import Any

COLORS = {
    "ink": "#172033",
    "muted": "#64748b",
    "blue": "#2563eb",
    "red": "#dc2626",
    "green": "#059669",
    "orange": "#ea580c",
    "paper": "#ffffff",
    "soft-blue": "#dbeafe",
    "soft-orange": "#ffedd5",
    "none": "none",
}


def number(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def color(value: Any, default: str) -> str:
    raw = str(value or default)
    resolved = COLORS.get(raw, raw)
    if not (resolved == "none" or resolved.startswith("#")):
        raise ValueError(f"Unsupported color: {raw}")
    return resolved


def attrs(item: dict[str, Any], *, fill: str = "none", stroke: str = "ink") -> str:
    values = {
        "fill": color(item.get("fill"), fill),
        "stroke": color(item.get("stroke"), stroke),
        "stroke-width": number(item.get("stroke_width", 2), "stroke_width"),
    }
    if item.get("dash"):
        values["stroke-dasharray"] = html.escape(str(item["dash"]), quote=True)
    return " ".join(f'{key}="{value}"' for key, value in values.items())


def points(value: Any) -> str:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError("points must contain at least two [x, y] pairs")
    return " ".join(f"{number(pair[0], 'x')},{number(pair[1], 'y')}" for pair in value)


def render_shape(item: dict[str, Any]) -> str:
    kind = item.get("type")
    common = attrs(item)
    if kind == "circle":
        return f'<circle cx="{number(item["cx"], "cx")}" cy="{number(item["cy"], "cy")}" r="{number(item["r"], "r")}" {common}/>'
    if kind == "rect":
        radius = number(item.get("rx", 0), "rx")
        return f'<rect x="{number(item["x"], "x")}" y="{number(item["y"], "y")}" width="{number(item["width"], "width")}" height="{number(item["height"], "height")}" rx="{radius}" {common}/>'
    if kind in {"line", "arrow"}:
        marker = ' marker-end="url(#arrowhead)"' if kind == "arrow" else ""
        return f'<line x1="{number(item["x1"], "x1")}" y1="{number(item["y1"], "y1")}" x2="{number(item["x2"], "x2")}" y2="{number(item["y2"], "y2")}" {common}{marker}/>'
    if kind in {"polyline", "polygon"}:
        return f'<{kind} points="{points(item["points"])}" {common}/>'
    if kind == "path":
        raw = str(item.get("d", ""))
        if not re_path_is_safe(raw):
            raise ValueError("path contains unsupported characters")
        marker = ' marker-end="url(#arrowhead)"' if item.get("arrow") else ""
        return f'<path d="{html.escape(raw, quote=True)}" {common}{marker}/>'
    if kind == "text":
        size = number(item.get("size", 18), "size")
        anchor = str(item.get("anchor", "middle"))
        if anchor not in {"start", "middle", "end"}:
            raise ValueError("text anchor must be start, middle, or end")
        weight = "700" if item.get("bold") else "400"
        fill_color = color(item.get("color"), "ink")
        content = html.escape(str(item.get("text", "")))
        return (
            f'<text x="{number(item["x"], "x")}" y="{number(item["y"], "y")}" '
            f'text-anchor="{anchor}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill_color}" stroke="none">{content}</text>'
        )
    raise ValueError(f"Unsupported shape type: {kind}")


def re_path_is_safe(value: str) -> bool:
    allowed = set("0123456789.,-+ eEMmLlHhVvCcSsQqTtAaZz")
    return bool(value.strip()) and all(character in allowed for character in value)


def render(spec: dict[str, Any]) -> str:
    width = int(number(spec.get("width", 800), "width"))
    height = int(number(spec.get("height", 500), "height"))
    if not (100 <= width <= 2400 and 100 <= height <= 2400):
        raise ValueError("canvas dimensions must be between 100 and 2400")
    background = color(spec.get("background"), "paper")
    title = html.escape(str(spec.get("title", "Educational diagram")), quote=True)
    shapes = spec.get("shapes", [])
    if not isinstance(shapes, list) or not shapes:
        raise ValueError("spec.shapes must be a non-empty list")
    body = "\n  ".join(render_shape(item) for item in shapes)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{title}">
  <title>{title}</title>
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
      <polygon points="0 0, 10 3.5, 0 7" fill="#2563eb" stroke="none"/>
    </marker>
  </defs>
  <rect width="100%" height="100%" fill="{background}" stroke="none"/>
  {body}
</svg>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path, help="JSON scene specification")
    parser.add_argument("output", type=Path, help="Output .svg path")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    svg = render(spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "shapes": len(spec["shapes"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
