#!/usr/bin/env python3
"""Explicit, optional diagram plugins.

Plugins in this module are never selected as a fallback.  A caller must name a
plugin ID deliberately; the production answer pipeline has no default plugin.
"""

from __future__ import annotations

import html
import re
from typing import Any

FLOWCHART_PLUGIN_ID = "logic-flowchart"


def available_plugins() -> tuple[str, ...]:
    return (FLOWCHART_PLUGIN_ID,)


def render_optional_diagram(plugin_id: str, payload: dict[str, Any]) -> str:
    """Render one explicitly selected optional plugin or reject the request."""
    if plugin_id != FLOWCHART_PLUGIN_ID:
        raise ValueError(f"unsupported diagram plugin: {plugin_id or '<none>'}")
    return _render_logic_flowchart(payload)


def _render_logic_flowchart(payload: dict[str, Any]) -> str:
    title = str(payload.get("title", "")).strip()[:120] or "解题逻辑"
    raw_nodes = payload.get("nodes")
    nodes = [str(item).strip()[:80] for item in raw_nodes or [] if str(item).strip()][:6]
    if len(nodes) < 2:
        raise ValueError("logic-flowchart requires at least two nodes")
    width = 960
    margin = 36
    gap = 24
    node_width = max(110, (width - 2 * margin - gap * (len(nodes) - 1)) // len(nodes))
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="170" viewBox="0 0 960 170">',
        '<rect width="960" height="170" rx="18" fill="#f8fafc"/>',
        f'<text x="480" y="30" text-anchor="middle" font-size="20" font-family="sans-serif" '
        f'font-weight="700" fill="#0f172a">{html.escape(title)}</text>',
        '<defs><marker id="arrow" markerWidth="9" markerHeight="7" refX="8" refY="3.5" '
        'orient="auto"><path d="M0,0 L9,3.5 L0,7 Z" fill="#64748b"/></marker></defs>',
    ]
    for index, label in enumerate(nodes):
        x = margin + index * (node_width + gap)
        parts.append(
            f'<rect x="{x}" y="58" width="{node_width}" height="72" rx="12" '
            'fill="#e0f2fe" stroke="#0284c7" stroke-width="2"/>'
        )
        words = re.findall(r".{1,12}", label)[:3]
        first_y = 88 - 10 * (len(words) - 1)
        for offset, word in enumerate(words):
            parts.append(
                f'<text x="{x + node_width / 2:.1f}" y="{first_y + offset * 21}" '
                'text-anchor="middle" font-size="15" font-family="sans-serif" '
                f'fill="#0f172a">{html.escape(word)}</text>'
            )
        if index < len(nodes) - 1:
            parts.append(
                f'<line x1="{x + node_width + 4}" y1="94" '
                f'x2="{x + node_width + gap - 5}" y2="94" stroke="#64748b" '
                'stroke-width="2" marker-end="url(#arrow)"/>'
            )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
