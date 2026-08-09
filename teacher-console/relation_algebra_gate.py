"""Optional algebra-consistency gate for core-first candidates (experimental).

This module is an ISOLATED, feature-flagged experiment: it is only active when
the environment variable ``WULI_RELATION_ALGEBRA_GATE`` is set to a truthy
value, and it degrades to a no-op when ``sympy`` is unavailable. Turning the
flag off restores the previous gate behaviour exactly (zero-cost rollback).

Motivation (from the 4-failure coaxial-cylinder postmortem): a candidate's
``key_relations`` often split the governing equation and the solved value into
*different* relations (the equation appears in one relation, the ``解得`` value
in another), so a single-relation "由…得…解得…" pattern extracts nothing. The
only deterministic way to catch a silently swapped physical model is to gather
every constraint equation mentioning the final answer's variable, solve each
for that variable, and reject when NONE of them reproduces the declared value.

False-positive controls:
- definition relations (``L1 = m r1^2 ω``) are skipped: their left side is a
  single fresh symbol, not a constraint on the focus variable;
- only targets whose final answer is a plain ``var = expr`` equality are
  checked; inequality/worded finals are left to the existing gates;
- if ANY constraint equation solves to the declared value the target passes,
  so a lone intermediate equation cannot trigger a rejection;
- any sympy parse/solve failure silently skips that equation (never rejects).
"""

from __future__ import annotations

import os
import re
from typing import Any

try:  # pragma: no cover - exercised only where sympy is installed
    from sympy import Eq, simplify, solve, symbols, sympify

    _SYMPY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SYMPY_AVAILABLE = False

GATE_ENV = "WULI_RELATION_ALGEBRA_GATE"
RETRY_ENV = "WULI_RELATION_ALGEBRA_RETRY"
ALGEBRA_CODE = "relation-algebra-inconsistent"
MAX_FEEDBACK_CHARS = 3000
MAX_FEEDBACK_EQUATIONS = 3

GREEK_TO_NAME = {
    "ε0": "eps0",
    "μ0": "mu0",
    "ω": "omega",
    "Ω": "Omega",
    "μ": "mu",
    "π": "pi",
    "ε": "eps",
    "θ": "theta",
    "λ": "lam",
    "φ": "phi",
    "Δ": "Delta",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
}

_SYMBOL_CHARS = "A-Za-zα-ωΑ-Ωεμ"
_SYMBOL = rf"[{_SYMBOL_CHARS}][0-9]?"


def gate_enabled() -> bool:
    """True only when the experiment flag is explicitly switched on."""
    return os.environ.get(GATE_ENV, "").strip().lower() in ("1", "on", "true", "yes")


def retry_enabled() -> bool:
    """Corrective single-retry switch (layer-three), independent of the gate."""
    return os.environ.get(RETRY_ENV, "").strip().lower() in ("1", "on", "true", "yes")


def should_retry(gateway_result: dict[str, Any], reject_reason: str = "") -> bool:
    """True only for a failed run rejected specifically by the algebra gate.

    ``reject_reason`` is the materializer exception text captured by the
    caller: the gateway envelope only stores ``materializer_error: true``
    (a boolean), so the error text never survives into the result dict.
    """
    if not retry_enabled():
        return False
    if gateway_result.get("status") == "completed":
        return False
    return ALGEBRA_CODE in str(reject_reason or "")


def _prep(text: str) -> str:
    """Normalize Greek/subscript tokens and insert implicit multiplication.

    The multiplication rules use look-aheads and iterate to a fixed point so a
    replacement never consumes the character after the whitespace (the naive
    ``re.sub`` scan otherwise skips past ``m r1`` once it rewrites ``2 m``).
    """
    normalized = text
    for glyph, name in GREEK_TO_NAME.items():
        normalized = normalized.replace(glyph, name)
    normalized = normalized.replace("^", "**").replace("≥", ">=").replace("≤", "<=")
    normalized = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", normalized)
    normalized = re.sub(r"\\[a-zA-Z]+", "", normalized)
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = re.sub(r"(\d)\s*(?=[A-Za-z(])", r"\1*", normalized)
        normalized = re.sub(r"\)\s*(?=[A-Za-z0-9(])", r")*", normalized)
        normalized = re.sub(r"([A-Za-z0-9])\s+(?=[A-Za-z0-9(])", r"\1*", normalized)
    return normalized


def _parse(text: str) -> Any:
    """Parse to a sympy expression; ``None`` on any failure (never raises)."""
    if not _SYMPY_AVAILABLE:
        return None
    try:
        return sympify(_prep(text))
    except Exception:
        return None


def _strip_uncertain_tail(text: str) -> str:
    """Keep only the portion before an uncertainty marker.

    Relations like ``由角动量守恒得 m r1^2 ω + m r2^2 Ω = 0？但需考虑…`` still
    carry a well-defined equation before the ``？``; only the hedging tail is
    dropped so the equation remains available to the pool.
    """
    for marker in ("？", "?"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text


def _strip_parenthetical(text: str) -> str:
    """Remove full-width Chinese annotation brackets only.

    Half-width parentheses carry mathematics (``(2\u03c0)``, ``(\u03a9-\u03c9)``) and must survive;
    only the full-width prose annotation on a final answer (``（与 Ω 同向）``) is stripped.
    """
    return re.sub(r"（[^（）]*）", "", text)


def _is_definition(lhs_text: str) -> bool:
    """A bare single-symbol left side (``L1``, ``F_e``) defines, not constrains."""
    return re.fullmatch(_SYMBOL, lhs_text.strip()) is not None


def _focus_of_final(final_answer: str) -> tuple[str, Any] | None:
    """Extract ``(focus_symbol, declared_value)`` from a ``var = expr`` final.

    Returns ``None`` for inequality or worded finals, which this gate leaves to
    the existing checks.
    """
    match = re.search(rf"({_SYMBOL})\s*=\s*(.+)$", final_answer)
    if not match:
        return None
    focus_symbol = _prep(match.group(1))
    declared_text = match.group(2)
    # Chained equalities (``J = A = B = C``) declare the final value after the
    # last ``=``; multi-clause finals are left unparsable and skipped.
    if "=" in declared_text:
        declared_text = declared_text.rsplit("=", 1)[1]
    declared_text = _strip_parenthetical(declared_text)
    declared_text = re.sub(r"[。；，、\s]+$", "", declared_text)
    declared = _parse(declared_text)
    if declared is None:
        return None
    return focus_symbol, declared


def _strip_prose_prefix(text: str) -> str:
    """Drop leading Chinese/prose words so ``由角动量守恒得 m r1^2 ω`` → ``m r1^2 ω``."""
    return re.sub(rf"^[^{_SYMBOL_CHARS}0-9(]+", "", text.strip())


def _constraint_equations(relation: str, focus_symbol: str) -> list[tuple[Any, Any]]:
    """Constraint equations in one relation that mention the focus symbol."""
    cleaned = _strip_parenthetical(_strip_uncertain_tail(relation))
    equations: list[tuple[Any, Any]] = []
    for segment in re.split(r"[，。；,;]", cleaned):
        if "=" not in segment:
            continue
        for match in re.finditer(r"([^=，。；]+?)\s*=\s*([^=，。；]+)", segment):
            lhs_text = _strip_prose_prefix(match.group(1))
            rhs_text = _strip_prose_prefix(match.group(2))
            if not lhs_text or not rhs_text or _is_definition(lhs_text):
                continue
            lhs = _parse(lhs_text)
            rhs = _parse(rhs_text)
            if lhs is None or rhs is None:
                continue
            if not any(symbol.name == focus_symbol for symbol in lhs.free_symbols):
                continue
            equations.append((lhs, rhs))
    return equations


def algebra_violations(target: dict[str, Any]) -> list[dict[str, str]]:
    """Reject when no constraint equation reproduces the declared final value."""
    if not gate_enabled() or not _SYMPY_AVAILABLE:
        return []
    final_answer = str(target.get("final_answer", ""))
    focus = _focus_of_final(final_answer)
    if focus is None:
        return []
    focus_symbol, declared = focus

    equations: list[tuple[Any, Any]] = []
    for relation in target.get("key_relations", []):
        equations.extend(_constraint_equations(str(relation), focus_symbol))
    if not equations:
        return []

    symbol = symbols(focus_symbol)
    for lhs, rhs in equations:
        try:
            solutions = solve(Eq(lhs, rhs), symbol)
        except Exception:
            continue
        for solution in solutions:
            try:
                if simplify(solution - declared) == 0:
                    return []  # at least one self-consistent derivation path
            except Exception:
                continue

    return [
        {
            "code": ALGEBRA_CODE,
            "target_id": str(target.get("id", "")),
            "detail": (
                f"no constraint equation over {focus_symbol} reproduces the declared "
                f"final value; the solved value cannot be derived from the stated relations"
            ),
        }
    ]


def _feedback_for_target(target: dict[str, Any]) -> str:
    """One corrective block: the equations, their exact solutions, the fix rule."""
    focus = _focus_of_final(str(target.get("final_answer", "")))
    if focus is None or not _SYMPY_AVAILABLE:
        return ""
    focus_symbol, declared = focus
    equations: list[tuple[Any, Any]] = []
    for relation in target.get("key_relations", []):
        equations.extend(_constraint_equations(str(relation), focus_symbol))
    if not equations:
        return ""
    symbol = symbols(focus_symbol)
    solved: list[str] = []
    for lhs, rhs in equations[:MAX_FEEDBACK_EQUATIONS]:
        try:
            solutions = solve(Eq(lhs, rhs), symbol)
        except Exception:
            continue
        if solutions:
            solved.append(f"「{lhs} = {rhs}」解得 {focus_symbol} = {solutions[0]}")
    if not solved:
        return ""
    lines = [
        f"目标 {target.get('id', '')}：final_answer 声明的 {focus_symbol} 值（{declared}）"
        f"无法由 key_relations 中的约束方程推出。",
    ]
    lines.extend(f"- {item}" for item in solved)
    lines.append(
        "修正要求：final_answer 必须严格从上述方程重新求解得出；"
        "若你认为还需要其他物理方程，必须先在 key_relations 中完整写出该方程及其依据，"
        "禁止在 key_relations 之外直接给出新结果。"
    )
    return "\n".join(lines)


def corrective_feedback(claims: list[dict[str, Any]]) -> str:
    """Corrective prompt text for one bounded retry; empty when nothing to say."""
    if not retry_enabled() or not gate_enabled() or not _SYMPY_AVAILABLE:
        return ""
    blocks = [block for block in (_feedback_for_target(claim) for claim in claims) if block]
    if not blocks:
        return ""
    text = (
        "\n\n【代数一致性修正（一次性重试）】上一轮输出被代数一致性门拒绝，"
        "问题与修正要求如下：\n" + "\n".join(blocks)
    )
    return text[:MAX_FEEDBACK_CHARS]
