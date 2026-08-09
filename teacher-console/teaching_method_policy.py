#!/usr/bin/env python3
"""Auditable method profiles for standard teaching and official competitions."""

from __future__ import annotations

import re
from typing import Any

HIGH_SCHOOL_STANDARD = "high_school_standard"
OLYMPIAD_OFFICIAL = "olympiad_official"
DEFAULT_PROFILE = HIGH_SCHOOL_STANDARD

# Applied-method signals only. The literal \int / ∫ symbols and verb-adjacent
# prose ("使用积分", "对时间求导") are evidence the solution applies the method;
# bare mentions inside tips, definitions ("力矩对时间的积分") or method-comparison
# notes are not. A "的" between verb and term marks the nominal/definitional
# form and breaks the applied-method window.
_METHOD_PATTERNS = (
    (
        re.compile(r"\\int(?![a-z])|∫|(?:使用|采用|利用|通过|运用|用|对)[^。；;，,的\n]{0,12}?积分"),
        "calculus.integration",
        "积分",
    ),
    (
        re.compile(r"(?:使用|采用|利用|通过|运用|用|对)[^。；;，,的\n]{0,12}?(?:导数|求导|微分)"),
        "calculus.differentiation",
        "导数或微分",
    ),
    (re.compile(r"矩阵|行列式"), "linear_algebra", "矩阵或行列式"),
    (re.compile(r"复数法|欧拉公式"), "complex_numbers", "复数法"),
    (re.compile(r"拉格朗日|哈密顿"), "university_mechanics", "大学力学方法"),
)

# Mentions preceded by a negation cue are guidance *against* the method
# ("无需积分，用动能定理即可", "不要使用导数"), not evidence that the solution
# applies it. They are masked before matching so deterministic rejection only
# fires on genuine use.
_NEGATION_MASK = re.compile(
    r"(?:不用|不需要|不要|无需|避免|未使用|不使用|不能|不可|不必)"
    r"[^。；;，,\n]{0,12}?"
    r"(积分|导数|求导|微分|矩阵|行列式|复数法|欧拉公式|拉格朗日|哈密顿)"
)

PROFILES: dict[str, dict[str, Any]] = {
    HIGH_SCHOOL_STANDARD: {
        "level": "high_school",
        "max_main_steps": 5,
        "forbidden_methods": [
            "calculus.integration",
            "calculus.differentiation",
            "linear_algebra",
            "complex_numbers",
            "university_mechanics",
        ],
    },
    OLYMPIAD_OFFICIAL: {
        "level": "olympiad_official",
        "max_main_steps": 5,
        # Calculus and equivalent official-competition tools are permitted.
        # University analytical-mechanics formalisms remain outside the
        # default profile unless a future source-specific profile authorizes them.
        "forbidden_methods": ["university_mechanics"],
    },
}


def normalize_profile(value: Any) -> str:
    profile = str(value or DEFAULT_PROFILE).strip().lower()
    if profile not in PROFILES:
        raise ValueError("method_profile must be high_school_standard or olympiad_official")
    return profile


def scope(value: Any) -> dict[str, Any]:
    profile = normalize_profile(value)
    return {"profile": profile, **PROFILES[profile]}


def method_errors(text: str, value: Any = DEFAULT_PROFILE) -> list[str]:
    profile = normalize_profile(value)
    forbidden = set(PROFILES[profile]["forbidden_methods"])
    scan_text = _NEGATION_MASK.sub("", text)
    return [
        (
            f"student_solution uses non-high-school method: {label}"
            if profile == HIGH_SCHOOL_STANDARD
            else f"student_solution uses method forbidden by {profile}: {label}"
        )
        for pattern, method_id, label in _METHOD_PATTERNS
        if method_id in forbidden and pattern.search(scan_text)
    ]
