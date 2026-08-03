#!/usr/bin/env python3
"""Shared fail-closed validation for Agent-produced structured text."""

from __future__ import annotations

import re
from typing import Any

_UNSUPPORTED_CONTROL_PATTERN = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]")


def reject_unsupported_controls(value: Any, field: str) -> str:
    """Return a string only when it contains no unsupported C0/DEL controls."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if _UNSUPPORTED_CONTROL_PATTERN.search(value):
        raise ValueError(f"{field} contains unsupported control characters")
    return value
