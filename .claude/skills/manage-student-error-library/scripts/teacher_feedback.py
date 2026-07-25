#!/usr/bin/env python3
"""Deterministic, privacy-minimized teacher-feedback signals.

These helpers do not grade physics correctness.  They turn observable teacher
actions into auditable categories and adoption estimates; final evaluation
still comes from the teacher-approved artifact.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

ADOPTION_LEVELS = ("redo", "major-revision", "minor-revision", "as-is")

_CATEGORY_PATTERNS = {
    "physics-correction": re.compile(r"公式|结论|数值|计算|方向|正负|单位|边界|条件|定律|受力|速度|加速度"),
    "reasoning-gap": re.compile(r"推理|步骤|论证|解释|依据|为什么|跳步|补充"),
    "pedagogy": re.compile(r"学生|易懂|提示|引导|分层|认知|课堂|讲解|例子|简化"),
    "taxonomy": re.compile(r"知识点|错因|难度|年级|标签|分类"),
    "visualization": re.compile(r"可视化|仿真|图|箭头|坐标|轨迹|动画|交互"),
    "formatting": re.compile(r"格式|排版|标题|错别字|标点|LaTeX|markdown", re.IGNORECASE),
}
_MARKDOWN_ONLY = re.compile(r"[#*_>`~\-\s]+")
_PHYSICS_TOKENS = re.compile(
    r"(?:\\frac|\\sqrt|\\vec|[=<>±≈∝∑∫]|(?:^|[\s(])[-+]?\d+(?:\.\d+)?(?:[A-Za-zΩ·/^²³]+)?)"
)


def feedback_categories(note: str, *, changed_files: list[str] | None = None) -> list[str]:
    categories = {name for name, pattern in _CATEGORY_PATTERNS.items() if pattern.search(note or "")}
    for path in changed_files or []:
        lowered = path.casefold()
        if lowered.endswith((".svg", ".png", ".jpg", ".jpeg", ".webp", "physics-model.json")):
            categories.add("visualization")
        if lowered.endswith("record.json"):
            categories.add("taxonomy")
    return sorted(categories or {"other"})


def semantic_text_diff(old: str, new: str, *, path: str = "") -> dict[str, Any]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    changed = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag != "equal":
            changed += max(i2 - i1, j2 - j1)
    old_plain = _MARKDOWN_ONLY.sub("", old)
    new_plain = _MARKDOWN_ONLY.sub("", new)
    categories: set[str] = set()
    if old_plain == new_plain and old != new:
        categories.add("formatting")
    elif old != new:
        categories.add("reasoning")
        delta_text = "\n".join(difflib.ndiff(old_lines, new_lines))
        if _PHYSICS_TOKENS.search(delta_text):
            categories.add("physics-correction")
        if re.search(r"易错|提示|学生|理解|方法|课堂|分层", delta_text):
            categories.add("pedagogy")
    categories.update(set(feedback_categories("", changed_files=[path])) - {"other"})
    return {
        "changed_lines": changed,
        "total_lines": max(len(old_lines), len(new_lines), 1),
        "categories": sorted(categories),
        "critical_correction": "physics-correction" in categories,
    }


def infer_adoption(
    diff: dict[str, Any] | None,
    *,
    revision_requests: int = 0,
    final_status: str = "approved",
) -> dict[str, Any]:
    diff = diff or {}
    ratio = float(diff.get("change_ratio") or 0.0)
    categories = set(diff.get("categories") or [])
    critical = bool(diff.get("critical_correction")) or "physics-correction" in categories
    if final_status != "approved" or critical or revision_requests >= 2 or ratio >= 0.4:
        level = "redo"
    elif revision_requests >= 1 or ratio >= 0.15:
        level = "major-revision"
    elif ratio > 0 or int(diff.get("changed_lines") or 0) > 0:
        level = "minor-revision"
    else:
        level = "as-is"
    evidence = {
        "change_ratio": round(ratio, 4),
        "changed_lines": int(diff.get("changed_lines") or 0),
        "revision_requests": max(0, int(revision_requests)),
        "critical_correction": critical,
        "categories": sorted(categories),
    }
    confidence = 0.9 if diff.get("status") == "computed" else (0.72 if revision_requests else 0.55)
    return {
        "level": level,
        "confidence": confidence,
        "inferred": True,
        "evidence": evidence,
    }
