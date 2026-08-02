#!/usr/bin/env python3
"""Auditable method profiles for standard teaching and official competitions."""

from __future__ import annotations

import re
from typing import Any

HIGH_SCHOOL_STANDARD = "high_school_standard"
OLYMPIAD_OFFICIAL = "olympiad_official"
DEFAULT_PROFILE = HIGH_SCHOOL_STANDARD

_METHOD_PATTERNS = (
    (re.compile(r"\\int\b|积分"), "calculus.integration", "积分"),
    (re.compile(r"导数|求导|微分(?:方程)?"), "calculus.differentiation", "导数或微分"),
    (re.compile(r"矩阵|行列式"), "linear_algebra", "矩阵或行列式"),
    (re.compile(r"复数法|欧拉公式"), "complex_numbers", "复数法"),
    (re.compile(r"拉格朗日|哈密顿"), "university_mechanics", "大学力学方法"),
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
        raise ValueError(
            "method_profile must be high_school_standard or olympiad_official"
        )
    return profile


def scope(value: Any) -> dict[str, Any]:
    profile = normalize_profile(value)
    return {"profile": profile, **PROFILES[profile]}


def method_errors(text: str, value: Any = DEFAULT_PROFILE) -> list[str]:
    profile = normalize_profile(value)
    forbidden = set(PROFILES[profile]["forbidden_methods"])
    return [
        (
            f"student_solution uses non-high-school method: {label}"
            if profile == HIGH_SCHOOL_STANDARD
            else f"student_solution uses method forbidden by {profile}: {label}"
        )
        for pattern, method_id, label in _METHOD_PATTERNS
        if method_id in forbidden and pattern.search(text)
    ]
