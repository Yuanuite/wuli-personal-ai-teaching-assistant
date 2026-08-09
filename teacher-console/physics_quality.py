"""Deterministic physics quality gate for core-first candidates (work-tree A0.2/A1.2).

``physics_quality_report`` checks a normalized core candidate against a small set
of deterministic obligations derived from the two observed failure classes:

- ``derivation-answer-mismatch``: the same variable pair is expressed in a log
  form (``\\ln``/``\\log``) for an energy/work relation while the final answer
  uses a reciprocal-difference form (``1/x - 1/y``), i.e. the derivation and the
  conclusion cannot both hold for the same quantity.
- ``sign-flip-unjustified``: a derivation relation claims the problem states a
  direction (``同向``) while the solved value is explicitly negative and the
  final answer silently drops the sign (``取绝对值``) without a conditional.
- ``internal-recheck-conflict``: a ``复核``/``验证`` relation gives an
  unconditionally negative explicit expression while the final answer is
  positive, i.e. the candidate contradicts itself.
- ``symbol-undefined``: a subscripted symbol (``r1``, ``ε0``) used in the final
  answer appears neither in the problem text nor in the target's derivation.

Obligations that cannot be decided deterministically (dimension, applicability
conditions) are recorded as ``deferred-verifier`` and never block promotion —
they are the independent verifier's obligation (work-tree A0.2 fallback rule).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import relation_algebra_gate

PHYSICS_QUALITY_CONTRACT = "wuli.physics-quality-gate.v1"

# Tokens that mark a derivation relation as the energy/work statement carrying a
# log-form expression of the same variable pair the final answer treats as a
# reciprocal difference.
_ENERGY_MARKERS = ("做功", "能量", "功 ", "W_E", "W=", "电势差", "电场力做")

_NEGATIVE_SIGN = ("负号", "反向", "反方向")
_SIGN_REWRITE = ("取绝对值", "绝对值")

_RECHECK_MARKERS = ("复核", "验证", "检验", "检查")

_SYMBOL_TOKEN = re.compile(r"([A-Za-zα-ωΑ-Ωε][0-9]|[α-ωΑ-Ωε][A-Za-z]?[0-9])")

# Roman sub-question suffixes that target ids may carry (``Q4i``/``Q4ii``): a
# ``letter+digit`` token immediately followed by such a suffix is a
# cross-target reference, not a physics symbol.
_ROMAN_SUFFIX = re.compile(r"[A-Za-zα-ωΑ-Ωε][0-9][ivxl]+", re.IGNORECASE)

# Context words that mark a ``Q\d``-style token as a question reference
# (``上一问的结果``) rather than a physics quantity.
_QUESTION_CONTEXT = ("第", "问", "上问", "小问", "(i)", "(ii)", "（i）", "（ii）")

_GREEK_COMMANDS = {
    "varepsilon": "ε",
    "epsilon": "ε",
    "mu": "μ",
    "omega": "ω",
    "Omega": "Ω",
    "pi": "π",
    "theta": "θ",
    "lambda": "λ",
    "phi": "φ",
    "Delta": "Δ",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "Gamma": "Γ",
    "sigma": "σ",
    "tau": "τ",
    "rho": "ρ",
    "Phi": "Φ",
}


def _normalize_latex(text: str) -> str:
    """Strip LaTeX scaffolding so subscript tokens compare textually.

    ``\\frac{1}{x}`` is preserved as ``1/x`` so reciprocal-difference patterns
    survive normalization; Greek command names are mapped to their glyphs first
    so ``\\varepsilon_0`` becomes ``ε0`` instead of a spurious ``s0`` token.
    """
    normalized = text
    for command, glyph in _GREEK_COMMANDS.items():
        normalized = normalized.replace(f"\\{command}", glyph)
    normalized = re.sub(r"\\(?:frac|dfrac|tfrac)\{1\}\{([^{}]*)\}", r"1/\1", normalized)
    normalized = re.sub(r"\\[a-zA-Z]+\{([^{}]*)\}", r"\1", normalized)
    normalized = re.sub(r"([A-Za-zα-ωΑ-Ωε])_\{([^{}]*)\}", r"\1\2", normalized)
    normalized = re.sub(r"([A-Za-zα-ωΑ-Ωε])_([A-Za-z0-9]+)", r"\1\2", normalized)
    return normalized


def _explicit_negative(expression: str) -> bool:
    """True when the text carries an explicit leading minus on a formula."""
    if re.search(r"(?<![\w}])-\s*(?:\\frac|\\dfrac|\(|\\left\()", expression):
        return True
    if re.search(r"(?<![\w}])-\s*[A-Za-zα-ωΑ-Ωε(]", expression):
        return True
    return False


def _recheck_conclusion_negative(relation: str) -> bool:
    """True when the relation's concluding value is unconditionally negative.

    Only the final ``= ...`` segment counts as the conclusion: a negative term
    substituted earlier in the sentence (``代入 $\\omega = -x$，验证 $L=0$``)
    is part of a consistent verification, not a contradiction. Relations without
    an equation keep the whole-text scan so bare negative results still trip.
    """
    if "=" not in relation:
        return _explicit_negative(relation)
    conclusion = relation.rsplit("=", 1)[1].strip().lstrip("$").strip()
    return bool(re.match(r"-\s*(?:\\frac|\\dfrac|\(|\\left\(|[A-Za-zα-ωΑ-Ωε(])", conclusion))


def _reciprocal_difference_pairs(text: str) -> set[tuple[str, str]]:
    """Extract variable pairs in a ``1/x - 1/y`` shape from normalized text."""
    pairs: set[tuple[str, str]] = set()
    for match in re.finditer(
        r"(?:\\frac\{1\}\{([A-Za-zα-ωΑ-Ωε0-9]+)\}|1\s*/\s*([A-Za-zα-ωΑ-Ωε0-9]+))(?:\s*-\s*)(?:\\frac\{1\}\{([A-Za-zα-ωΑ-Ωε0-9]+)\}|1\s*/\s*([A-Za-zα-ωΑ-Ωε0-9]+))",
        text,
    ):
        left = match.group(1) or match.group(2)
        right = match.group(3) or match.group(4)
        if left and right and left != right:
            pairs.add((left, right) if left < right else (right, left))
    return pairs


def _log_expression_pairs(text: str) -> set[tuple[str, str]]:
    """Variable pairs that appear inside a ``ln``/``log`` expression.

    Both LaTeX (``\\ln``) and plain-text (``ln``) spellings are accepted because
    real candidates mix them.
    """
    pairs: set[tuple[str, str]] = set()
    for match in re.finditer(r"\\?(?:ln|log)\b", text):
        window = text[match.start() : match.start() + 48]
        stops = [
            index
            for index in (
                window.find(")"),
                window.find("。"),
                window.find("；"),
                window.find("，"),
            )
            if index != -1
        ]
        cut = min(stops) if stops else len(window)
        inner = window[: cut + 1]
        variables = re.findall(r"[A-Za-zα-ωΑ-Ωε][0-9]?", inner)
        for left in variables:
            for right in variables:
                if left != right:
                    pairs.add((left, right) if left < right else (right, left))
    return pairs


def _collect_known_target_ids(payload: dict[str, Any]) -> set[str]:
    """Target ids already normalized in the payload (``Q1``/``Q4i``/``Q4ii``)."""
    known: set[str] = set()
    targets = payload.get("targets")
    if isinstance(targets, list):
        for target in targets:
            if isinstance(target, dict):
                target_id = str(target.get("id", "")).strip()
                if target_id:
                    known.add(target_id)
    return known


def _is_question_reference(token: str, text: str) -> bool:
    r"""Heuristic fallback: ``Q\d`` tokens inside question-numbering context."""
    if not re.fullmatch(r"[Qq]\d+", token):
        return False
    for match in re.finditer(re.escape(token), text):
        window = text[max(0, match.start() - 8) : match.end() + 8]
        if any(marker in window for marker in _QUESTION_CONTEXT):
            return True
    return False


def _undefined_symbols(
    final_answer: str,
    definitions: str,
    known_target_ids: set[str] | None = None,
) -> list[str]:
    """Subscripted tokens in the final answer missing from the definitions text.

    Tokens that are (or prefix-match) known target ids are cross-target
    references like ``Q4i`` and never physics symbols; ``Q4`` inside the
    normalized text is skipped when the full ``Q4i`` form is a known target id.
    """
    undefined: list[str] = []
    normalized = _normalize_latex(final_answer)
    normalized_defs = _normalize_latex(definitions)
    known_ids = known_target_ids or set()
    for match in _SYMBOL_TOKEN.finditer(normalized):
        token = match.group(1).replace("_", "")
        if not token:
            continue
        full_token = normalized[match.start() : match.end()]
        roman = _ROMAN_SUFFIX.match(normalized, match.start())
        if roman:
            full_token = roman.group(0)
        if full_token in known_ids or token in known_ids:
            continue
        if any(known_id.startswith(token) for known_id in known_ids):
            continue
        if _is_question_reference(token, normalized):
            continue
        pattern = re.compile(r"(?<![A-Za-zα-ωΑ-Ωε0-9])" + re.escape(token) + r"(?![0-9])")
        if not pattern.search(normalized_defs):
            if token not in undefined:
                undefined.append(token)
    return undefined


def _check_symbol_defined(
    target: dict[str, Any],
    problem_text: str,
    known_target_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    definitions = problem_text + "\n" + "\n".join(target.get("key_relations", []))
    undefined = _undefined_symbols(str(target.get("final_answer", "")), definitions, known_target_ids)
    return [
        {
            "code": "symbol-undefined",
            "target_id": str(target.get("id", "")),
            "detail": f"symbol {token} is used but never defined",
        }
        for token in undefined
    ]


def _check_derivation_answer_mismatch(target: dict[str, Any]) -> list[dict[str, str]]:
    final = _normalize_latex(str(target.get("final_answer", "")))
    relations = [_normalize_latex(str(item)) for item in target.get("key_relations", [])]
    final_pairs = _reciprocal_difference_pairs(final)
    if not final_pairs:
        return []
    log_pairs: set[tuple[str, str]] = set()
    for relation in relations:
        # Only log expressions inside an energy/work statement count: a plain
        # geometric log elsewhere is a different quantity, not a conflict.
        if re.search(r"\\?(?:ln|log)\b", relation) and any(marker in relation for marker in _ENERGY_MARKERS):
            log_pairs |= _log_expression_pairs(relation)
    for pair in final_pairs:
        if pair in log_pairs:
            return [
                {
                    "code": "derivation-answer-mismatch",
                    "target_id": str(target.get("id", "")),
                    "detail": (
                        f"energy/work relation derives {pair[0]}/{pair[1]} in log form "
                        "but the final answer uses a reciprocal-difference form; a textual "
                        "restatement of the final form is not an algebraic bridge"
                    ),
                }
            ]
    return []


def _check_sign_flip_unjustified(target: dict[str, Any]) -> list[dict[str, str]]:
    relations = [str(item) for item in target.get("key_relations", [])]
    final = str(target.get("final_answer", ""))
    final_positive = not _explicit_negative(final) and "|" not in final
    if not final_positive:
        return []
    # The fabrication signature: a relation claims the problem states a
    # direction (题设同向) yet solves an explicitly negative value and rewrites
    # it with 取绝对值, while the final answer silently drops the sign without
    # stating the direction reconciliation. A legitimate answer states the
    # reconciliation (同向/反向/负号/方向) in the final answer itself.
    final_has_reconciliation = any(marker in final for marker in ("同向", "反向", "负号", "方向", "绝对值"))
    if final_has_reconciliation:
        return []
    for relation in relations:
        claims_problem_direction = ("题设同向" in relation) or ("题设同方向" in relation)
        has_negative = any(marker in relation for marker in _NEGATIVE_SIGN) or _explicit_negative(relation)
        has_rewrite = any(marker in relation for marker in _SIGN_REWRITE)
        if claims_problem_direction and has_negative and has_rewrite:
            return [
                {
                    "code": "sign-flip-unjustified",
                    "target_id": str(target.get("id", "")),
                    "detail": (
                        "derivation claims the problem states a direction yet solves a "
                        "negative value and the final answer drops the sign without stating the reconciliation"
                    ),
                }
            ]
    return []


def _check_internal_recheck_conflict(target: dict[str, Any]) -> list[dict[str, str]]:
    final = str(target.get("final_answer", ""))
    final_positive = not _explicit_negative(final) and "|" not in final
    if not final_positive:
        return []
    for relation in target.get("key_relations", []):
        relation = str(relation)
        if not any(marker in relation for marker in _RECHECK_MARKERS):
            continue
        if _recheck_conclusion_negative(relation) and "|" not in relation:
            return [
                {
                    "code": "internal-recheck-conflict",
                    "target_id": str(target.get("id", "")),
                    "detail": "复核/验证 relation gives an unconditionally negative expression while the final answer is positive",  # noqa: E501
                }
            ]
    return []


def physics_quality_report(
    payload: dict[str, Any],
    brief: dict[str, Any],
    problem_text: str,
) -> dict[str, Any]:
    """Return the deterministic physics quality report for a normalized candidate.

    The gate is intentionally narrow: obligations that cannot be decided
    deterministically are recorded as ``deferred-verifier`` instead of blocking.
    """
    reason_codes: list[dict[str, str]] = []
    targets = payload.get("targets") or []
    if not isinstance(targets, list):
        targets = []
    known_target_ids = _collect_known_target_ids(payload)
    for target in targets:
        if not isinstance(target, dict):
            continue
        reason_codes.extend(_check_symbol_defined(target, problem_text, known_target_ids))
        reason_codes.extend(_check_derivation_answer_mismatch(target))
        reason_codes.extend(_check_sign_flip_unjustified(target))
        reason_codes.extend(_check_internal_recheck_conflict(target))
        reason_codes.extend(relation_algebra_gate.algebra_violations(target))

    status = "fail" if reason_codes else "pass"
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    checked_obligations = [
        {"name": "symbol-definition", "status": "checked"},
        {"name": "derivation-conclusion-consistency", "status": "checked"},
        {"name": "sign-and-direction", "status": "checked"},
        {"name": "dimension", "status": "deferred-verifier"},
        {"name": "applicability-conditions", "status": "deferred-verifier"},
    ]
    if relation_algebra_gate.gate_enabled():
        checked_obligations.append({"name": "relation-algebra-consistency", "status": "checked"})
    return {
        "schema_version": 1,
        "contract": PHYSICS_QUALITY_CONTRACT,
        "status": status,
        "candidate_digest": digest,
        "checked_obligations": checked_obligations,
        "reason_codes": reason_codes,
    }
