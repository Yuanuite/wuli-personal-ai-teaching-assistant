#!/usr/bin/env python3
"""Independent deterministic W3R renderer and fidelity gate."""

from __future__ import annotations

import re
from typing import Any, Callable

import teaching_method_policy
import w3r_contract

REQUIRED_STUDENT_SECTIONS = (
    "答案速览",
    "一眼识别",
    "建模与符号",
    "详细解答",
    "分阶段详细推导",
    "结果与适用条件",
    "易错点",
    "30 秒自测",
)
PLACEHOLDER = re.compile(r"TODO|TBD|待补|占位|……|\.{3,}", re.IGNORECASE)


def _section_id(kind: str, target_id: str, index: int | None = None) -> str:
    suffix = f"-{index}" if index is not None else ""
    return f"{kind}-{target_id}{suffix}"


def _render_documents(brief: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    answers = {item["target_id"]: item for item in brief["final_answers"]}
    steps_by_target = {
        target["target_id"]: [step for step in brief["proof_skeleton"] if target["target_id"] in step["target_ids"]]
        for target in brief["question_targets"]
    }
    obligations_by_target = {
        target["target_id"]: [
            item for item in brief["verification_obligations"] if item["target_id"] == target["target_id"]
        ]
        for target in brief["question_targets"]
    }

    student = ["## 答案速览", ""]
    spans: list[dict[str, Any]] = []
    for index, target in enumerate(brief["question_targets"], 1):
        answer = answers[target["target_id"]]
        fragment = f"- （{index}）{answer['text']}"
        student.append(fragment)
        spans.append({
            "document": "student",
            "section_id": _section_id("answer", target["target_id"]),
            "claim_ids": answer["claim_ids"],
            "step_ids": [
                step["step_id"]
                for step in steps_by_target[target["target_id"]]
                if step["teaching_role"] == "conclusion"
            ],
            "text_fingerprint": w3r_contract.stable_fingerprint(fragment),
        })
    mainline = " → ".join(step["statement"] for step in brief["proof_skeleton"][:3])
    student.extend([
        "",
        "## 一眼识别",
        "",
        f"- **最短主线**：{mainline}",
        "",
        "## 建模与符号",
        "",
    ])
    if brief["allowed_symbols"]:
        student.append(
            "- 本解答沿用题干符号：" + "、".join(f"${symbol}$" for symbol in brief["allowed_symbols"]) + "。"
        )
    else:
        student.append("- 沿用题干与已验证证明中的符号约定，不另设新量。")

    student.extend([
        "",
        "## 详细解答",
        "",
        "## 分阶段详细推导",
        "",
    ])
    for index, step in enumerate(brief["proof_skeleton"], 1):
        target_markers = " ".join(f"【{target_id}】" for target_id in step["target_ids"])
        fragment_lines = [
            f"### 第 {index} 步",
            "",
            f"**对应目标**：{target_markers}",
            "",
            step["statement"],
        ]
        if step["formula_latex"]:
            fragment_lines.extend(["", f"$${step['formula_latex']}$$"])
        for condition in step["conditions"]:
            fragment_lines.append(f"- 成立条件：{condition}")
        fragment = "\n".join(fragment_lines)
        student.extend([fragment, ""])
        spans.append({
            "document": "student",
            "section_id": _section_id("detail", step["step_id"], index),
            "claim_ids": step["claim_ids"],
            "step_ids": [step["step_id"]],
            "text_fingerprint": w3r_contract.stable_fingerprint(fragment),
        })

    student.extend(["## 结果与适用条件", ""])
    for target in brief["question_targets"]:
        target_id = target["target_id"]
        answer = answers[target_id]
        student.append(f"- **{target_id}**：{answer['text']}")
        conditions = list(
            dict.fromkeys(condition for step in steps_by_target[target_id] for condition in step["conditions"])
        )
        for condition in conditions:
            student.append(f"  - 适用条件：{condition}")
        if target["response_mode"] == "enumerate_all":
            student.append("  - 完整性：以上已按题意枚举全部可行结果。")

    student.extend(["", "## 易错点", ""])
    for target in brief["question_targets"]:
        target_id = target["target_id"]
        for item in obligations_by_target[target_id]:
            student.append(f"- **{target_id}**：{item['check']}")
    if not brief["verification_obligations"]:
        student.append("- 注意逐项复算并检查适用条件。")
    student.extend([
        "",
        "## 30 秒自测",
        "",
        "遮住答案后，能否沿最短主线复述每个目标的结论及适用条件？",
    ])
    student_md = "\n".join(student).rstrip() + "\n"

    teacher = [
        student_md.rstrip(),
        "",
        "## 教师审计（不公开）",
        "",
        f"- Brief：`{w3r_contract.brief_fingerprint(brief)}`",
        "- 所有学生版决定性句子均由下列 verified Claim 支撑。",
    ]
    claim_by_id = {item["claim_id"]: item for item in brief["verified_claims"]}
    for claim_id in sorted(claim_by_id):
        item = claim_by_id[claim_id]
        teacher.append(
            f"- `{claim_id}`：{item['statement']}；证书 "
            + "、".join(f"`{certificate_id}`" for certificate_id in item["certificate_ids"])
        )
    teacher.extend(["", "### 验证义务与审核焦点", ""])
    for item in brief["verification_obligations"]:
        teacher.append(
            f"- `{item['obligation_id']}` / {item['target_id']} / "
            f"{'已闭合' if item['resolved'] else '未闭合'}：{item['check']}"
        )
    teacher_md = "\n".join(teacher).rstrip() + "\n"
    return student_md, teacher_md, spans


def _latex_violations(text: str) -> list[dict[str, Any]]:
    violations = []
    if text.count("$$") % 2:
        violations.append({"code": "latex-delimiter-unpaired"})
    without_blocks = text.replace("$$", "")
    if without_blocks.count("$") % 2:
        violations.append({"code": "latex-inline-delimiter-unpaired"})
    if re.search(r"\\frac(?!\s*\{[^{}]+\}\s*\{[^{}]+\})", text):
        violations.append({"code": "latex-frac-broken"})
    if re.search(r"\\(?:tag|notag)\b", text):
        violations.append({"code": "latex-unsupported-tag"})
    return violations


def _contiguous_text_fingerprints(text: str) -> set[str]:
    lines = text.splitlines()
    fingerprints = set()
    for start in range(len(lines)):
        # A mapped teaching span is deliberately small.  Bounding the window
        # keeps validation linear under adversarial, newline-heavy candidates.
        for end in range(start + 1, min(len(lines), start + 16) + 1):
            fragment = "\n".join(lines[start:end]).strip()
            if fragment:
                fingerprints.add(w3r_contract.stable_fingerprint(fragment))
    return fingerprints


def render_gate(
    brief_payload: Any,
    result_payload: Any,
) -> dict[str, Any]:
    """Check structure, final answers, target/condition retention and LaTeX."""
    preflight = w3r_contract.preflight_w3r_brief(brief_payload)
    if preflight["status"] != "pass":
        return {
            "status": "reject",
            "violations": preflight["violations"],
            "metrics": {
                "final_answer_fidelity": 0.0,
                "claim_support_coverage": 0.0,
                "condition_retention": 0.0,
                "target_coverage": 0.0,
                "latex_validity": 0.0,
                "unsupported_claim_rate": 0.0,
            },
        }
    brief = preflight["brief"]
    try:
        result = w3r_contract.normalize_render_result(result_payload)
    except ValueError as exc:
        return {
            "status": "retryable",
            "violations": [{"code": "invalid-render-result", "message": str(exc)}],
            "metrics": {},
        }
    violations: list[dict[str, Any]] = []
    retryable_codes: set[str] = set()
    student = result["student_solution_md"]

    for section in REQUIRED_STUDENT_SECTIONS:
        if f"## {section}" not in student:
            violations.append({"code": "required-section-missing", "section": section})
            retryable_codes.add("required-section-missing")
    if PLACEHOLDER.search(student):
        violations.append({"code": "placeholder-present"})
        retryable_codes.add("placeholder-present")
    if len(student.splitlines()) > 2_000:
        violations.append({"code": "render-line-count-exceeded"})
        retryable_codes.add("render-line-count-exceeded")
    profile = "olympiad_official" if brief["method_scope"]["level"] == "olympiad_official" else "high_school_standard"
    method_errors = teaching_method_policy.method_errors(student, profile)
    if method_errors:
        violations.append({
            "code": "student-method-out-of-scope",
            "messages": method_errors,
        })

    target_hits = 0
    final_hits = 0
    retained_conditions = 0
    all_conditions = list(
        dict.fromkeys(condition for step in brief["proof_skeleton"] for condition in step["conditions"])
    )
    for target in brief["question_targets"]:
        if f"【{target['target_id']}】" in student:
            target_hits += 1
        else:
            violations.append({
                "code": "target-missing",
                "target_id": target["target_id"],
            })
            retryable_codes.add("target-missing")
    for answer in brief["final_answers"]:
        if answer["answer_signature"] and answer["answer_signature"] in (w3r_contract.answer_signature(student)):
            final_hits += 1
        else:
            violations.append({
                "code": "final-answer-drift",
                "target_id": answer["target_id"],
            })
    for condition in all_conditions:
        if condition in student:
            retained_conditions += 1
        else:
            violations.append({
                "code": "condition-omitted",
                "condition_fingerprint": w3r_contract.stable_fingerprint(condition),
            })

    latex_issues = _latex_violations(student)
    violations.extend(latex_issues)
    retryable_codes.update(item["code"] for item in latex_issues)
    allowed_display_math = {
        re.sub(r"\s+", "", item["formula_latex"]) for item in brief["proof_skeleton"] if item["formula_latex"]
    }
    rendered_display_math = {
        re.sub(r"\s+", "", item.strip()) for item in re.findall(r"\$\$(.*?)\$\$", student, flags=re.DOTALL)
    }
    unknown_formulas = rendered_display_math - allowed_display_math
    if unknown_formulas:
        violations.append({
            "code": "formula-without-brief-source",
            "formula_fingerprints": sorted(w3r_contract.stable_fingerprint(item) for item in unknown_formulas),
        })
    known_claims = {item["claim_id"] for item in brief["verified_claims"]}
    known_steps = {item["step_id"] for item in brief["proof_skeleton"]}
    mapped_claims: set[str] = set()
    unknown_claims: set[str] = set()
    mapped_steps: set[str] = set()
    student_fingerprints = _contiguous_text_fingerprints(student)
    teacher_fingerprints = _contiguous_text_fingerprints(result["teacher_solution_md"])
    for span in result["claim_span_map"]:
        mapped_claims.update(set(span["claim_ids"]) & known_claims)
        unknown_claims.update(set(span["claim_ids"]) - known_claims)
        mapped_steps.update(set(span["step_ids"]) & known_steps)
        if set(span["step_ids"]) - known_steps:
            violations.append({"code": "unknown-proof-step"})
        document_fingerprints = student_fingerprints if span["document"] == "student" else teacher_fingerprints
        if span["text_fingerprint"] not in document_fingerprints:
            violations.append({
                "code": "claim-span-text-mismatch",
                "section_id": span["section_id"],
            })
    if unknown_claims:
        violations.append({
            "code": "unsupported-claim",
            "claim_ids": sorted(unknown_claims),
        })
    decisive_claims = {claim_id for step in brief["proof_skeleton"] for claim_id in step["claim_ids"]} | {
        claim_id for answer in brief["final_answers"] for claim_id in answer["claim_ids"]
    }
    missing_claims = decisive_claims - mapped_claims
    if missing_claims:
        violations.append({
            "code": "claim-span-missing",
            "claim_ids": sorted(missing_claims),
        })
        retryable_codes.add("claim-span-missing")
    missing_steps = known_steps - mapped_steps
    if missing_steps:
        violations.append({
            "code": "proof-step-span-missing",
            "step_ids": sorted(missing_steps),
        })
        retryable_codes.add("proof-step-span-missing")

    metrics = {
        "final_answer_fidelity": round(final_hits / len(brief["final_answers"]), 4) if brief["final_answers"] else 0.0,
        "claim_support_coverage": round(len(decisive_claims & mapped_claims) / len(decisive_claims), 4)
        if decisive_claims
        else 0.0,
        "condition_retention": round(retained_conditions / len(all_conditions), 4) if all_conditions else 1.0,
        "target_coverage": round(target_hits / len(brief["question_targets"]), 4),
        "latex_validity": 0.0 if latex_issues else 1.0,
        "unsupported_claim_rate": round(len(unknown_claims) / max(1, len(mapped_claims | unknown_claims)), 4),
    }
    if not violations:
        status = "pass"
    elif any(item["code"] not in retryable_codes for item in violations):
        status = "reject"
    else:
        status = "retryable"
    return {"status": status, "violations": violations, "metrics": metrics}


def render_w3r(brief_payload: Any, *, attempt: int = 1) -> dict[str, Any]:
    """Render the frozen Brief without tools, retrieval, CAS or solver calls."""
    preflight = w3r_contract.preflight_w3r_brief(brief_payload)
    brief_fp = preflight.get(
        "brief_fingerprint",
        w3r_contract.stable_fingerprint({"invalid_brief": brief_payload}),
    )
    if preflight["status"] != "pass":
        return {
            "schema": w3r_contract.RENDER_RESULT_SCHEMA,
            "status": "needs_render_material",
            "brief_fingerprint": brief_fp,
            "student_solution_md": "",
            "teacher_solution_md": "",
            "claim_span_map": [],
            "render_gate_report": {
                "status": "reject",
                "violations": preflight["violations"],
                "metrics": {},
            },
            "attempt": attempt,
        }
    brief = preflight["brief"]
    student, teacher, spans = _render_documents(brief)
    candidate = {
        "schema": w3r_contract.RENDER_RESULT_SCHEMA,
        "status": "completed",
        "brief_fingerprint": brief_fp,
        "student_solution_md": student,
        "teacher_solution_md": teacher,
        "claim_span_map": spans,
        "render_gate_report": {
            "status": "retryable",
            "violations": [],
            "metrics": {},
        },
        "attempt": attempt,
    }
    report = render_gate(brief, candidate)
    candidate["render_gate_report"] = report
    if report["status"] != "pass":
        candidate["status"] = "rejected"
    return w3r_contract.normalize_render_result(candidate)


def render_with_single_retry(
    brief_payload: Any,
    renderer: Callable[[dict[str, Any], int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Retry one expression-only failure with the exact same frozen Brief."""
    brief = w3r_contract.normalize_w3r_brief(brief_payload)
    fingerprint = w3r_contract.brief_fingerprint(brief)
    renderer = renderer or (lambda value, attempt: render_w3r(value, attempt=attempt))
    first = renderer(brief, 1)
    if first.get("brief_fingerprint") != fingerprint:
        raise ValueError("renderer changed or did not bind the frozen Brief")
    gate = render_gate(brief, first)
    first["render_gate_report"] = gate
    if gate["status"] != "retryable":
        return first
    second = renderer(brief, 2)
    if second.get("brief_fingerprint") != fingerprint:
        raise ValueError("retry did not reuse the exact frozen Brief")
    second["render_gate_report"] = render_gate(brief, second)
    return second
