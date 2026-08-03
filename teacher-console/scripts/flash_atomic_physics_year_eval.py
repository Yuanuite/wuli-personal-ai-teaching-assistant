#!/usr/bin/env python3
"""Blind atomic-physics-assertion ablation for the CPhO 2021 year set.

Each assertion makes one decisive physical judgment and names a falsifier.
Official solutions are never opened or sent by this runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
for path in (CONSOLE, CONSOLE / "providers", Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))

import core_analysis  # noqa: E402
import core_first_year_eval as year_eval  # noqa: E402
import openai_compatible_agent_adapter as api_adapter  # noqa: E402

ASSERTION_KINDS = (
    "system-boundary",
    "reference-frame",
    "state-or-stage",
    "interaction-direction",
    "law-scope",
    "conservation-scope",
    "boundary-or-stability",
    "energy-or-power-flow",
)
CHECK_KINDS = (
    "sign",
    "dimension",
    "limit",
    "event-order",
    "conservation",
    "region",
    "substitution",
    "authority-semantic",
)
HEDGING = re.compile(
    r"待|可能|也许|或许|需(?:要)?(?:确认|判断|检查|推导)|不确定|未确定|视情况|"
    r"may|might|perhaps|possibly|need to (?:check|determine)|uncertain",
    re.I,
)


ATOMIC_ASSERTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string", "maxLength": 240},
        "target_brief_digest": {"type": ["string", "null"]},
        "global_conventions": {
            "type": ["array", "null"],
            "items": {"type": "string", "maxLength": 240},
            "minItems": 1,
            "maxItems": 6,
        },
        "targets": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "maxLength": 20},
                    "assertions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string", "maxLength": 30},
                                "kind": {"type": "string", "enum": list(ASSERTION_KINDS)},
                                "decision": {"type": "string", "maxLength": 320},
                                "basis": {"type": "string", "maxLength": 240},
                                "check_kind": {"type": "string", "enum": list(CHECK_KINDS)},
                                "falsifier": {"type": "string", "maxLength": 260},
                            },
                            "required": [
                                "id",
                                "kind",
                                "decision",
                                "basis",
                                "check_kind",
                                "falsifier",
                            ],
                        },
                        "minItems": 2,
                        "maxItems": 5,
                    },
                },
                "required": ["id", "assertions"],
            },
            "minItems": 1,
            "maxItems": 12,
        },
    },
    "required": [
        "status",
        "message",
        "target_brief_digest",
        "global_conventions",
        "targets",
    ],
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize(payload: Any, brief: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("atomic assertion payload must be an object")
    fields = {
        "status",
        "message",
        "target_brief_digest",
        "global_conventions",
        "targets",
    }
    if set(payload) != fields:
        raise ValueError("atomic assertion fields do not match contract")
    if payload.get("status") != "completed":
        raise ValueError("atomic assertion analysis is unsupported")
    if payload.get("target_brief_digest") != brief["digest"]:
        raise ValueError("target brief digest mismatch")
    conventions = payload.get("global_conventions")
    if not isinstance(conventions, list) or not conventions:
        raise ValueError("global conventions must not be empty")
    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise ValueError("targets must be an array")
    expected = [item["id"] for item in brief["targets"]]
    actual = [str(item.get("id", "")) for item in targets if isinstance(item, dict)]
    if actual != expected:
        raise ValueError(f"target ids mismatch: expected={expected}; actual={actual}")
    assertion_ids: set[str] = set()
    for target in targets:
        assertions = target.get("assertions") if isinstance(target, dict) else None
        if not isinstance(assertions, list) or not 2 <= len(assertions) <= 5:
            raise ValueError("each target must contain two to five assertions")
        for assertion in assertions:
            if not isinstance(assertion, dict):
                raise ValueError("assertion must be an object")
            assertion_id = str(assertion.get("id", "")).strip()
            if not assertion_id or assertion_id in assertion_ids:
                raise ValueError("assertion ids must be unique and non-empty")
            assertion_ids.add(assertion_id)
            if assertion.get("kind") not in ASSERTION_KINDS:
                raise ValueError("unknown assertion kind")
            if assertion.get("check_kind") not in CHECK_KINDS:
                raise ValueError("unknown assertion check kind")
            for field in ("decision", "basis", "falsifier"):
                value = str(assertion.get(field, "")).strip()
                if not value:
                    raise ValueError(f"assertion {field} must not be empty")
                if HEDGING.search(value):
                    raise ValueError(f"assertion {field} contains unresolved hedging")
    return payload


def solve_assertions(
    problem: str,
    visual: dict,
    config: dict,
) -> tuple[dict, dict, float]:
    brief = core_analysis.build_target_brief(
        problem,
        method_profile="olympiad_official",
        has_visual_facts=True,
    )
    target_ids = ", ".join(item["id"] for item in brief["targets"])
    contract = {
        "name": "wuli.atomic-physics-assertions-ablation.v1",
        "schema": ATOMIC_ASSERTION_SCHEMA,
        "instructions": (
            "只做决定性物理状态判别，不求公式、数值或最终答案。每个 assertion 只作一个明确判断，"
            "并给出一条物理依据和一个能推翻该判断的具体检查。禁止使用‘可能’‘需确认’‘待判断’等"
            "不确定措辞；无法唯一判断时整个对象返回 unsupported。"
            "优先覆盖参考系与方向、区域或阶段、接触事件、守恒系统边界、稳定性、边界条件和能量流；"
            "不要复述题干，不要列无关定律，不要创建题目未给出的过程分支。"
            f"targets 必须且只能按顺序覆盖 {target_ids}；target_brief_digest 必须原样返回 {brief['digest']}。"
            "global_conventions 只冻结全题共用的坐标、正方向和系统边界。"
        ),
    }
    visual_facts = visual["visual_facts"]
    task = {
        "prompt": (
            "对下面竞赛题生成原子物理断言，不要解题。\n\n题干：\n"
            + problem
            + "\n\nMiMo 视觉事实：\n"
            + json.dumps(
                {
                    "printed_facts": visual_facts["printed_facts"],
                    "diagram_facts": visual_facts["diagram_facts"],
                    "uncertainties": visual_facts["uncertainties"],
                },
                ensure_ascii=False,
            )
        )
    }
    instruction = api_adapter.build_structured_instruction(task, contract, "")
    options = api_adapter.request_options(
        str(config["base_url"]),
        str(config["model"]),
        {
            "TEACHER_CONSOLE_AGENT_API_MAX_OUTPUT_TOKENS": "4000",
            "TEACHER_CONSOLE_AGENT_API_THINKING": "disabled",
        },
    )
    started = time.monotonic()
    candidate, response = api_adapter.call_chat_completion(
        base=str(config["base_url"]),
        model=str(config["model"]),
        instruction=instruction,
        api_key=str(config["api_key"]),
        timeout=90,
        options=options,
    )
    return normalize(candidate, brief), response, round(time.monotonic() - started, 3)


def run_question(
    number: int,
    entry: Path,
    visual_dir: Path,
    config: dict,
    private_dir: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        preflight = year_eval.source_preflight(entry)
        if preflight["download_needed"]:
            raise ValueError("source image is missing")
        visual_path = visual_dir / f"q{number:02d}-visual.json"
        if not visual_path.is_file():
            raise ValueError("reviewed MiMo visual facts are missing")
        visual = year_eval.read_json(visual_path)
        problem = (entry / "problem.md").read_text(encoding="utf-8")
        candidate, response, elapsed = solve_assertions(problem, visual, config)
        frozen = {
            "schema_version": 1,
            "question": number,
            "problem_sha256": sha256_bytes(problem.encode("utf-8")),
            "visual_sha256": sha256_bytes(visual_path.read_bytes()),
            "candidate": candidate,
            "flash_seconds": elapsed,
            "usage": api_adapter.normalized_usage(response),
        }
        path = private_dir / f"q{number:02d}.json"
        path.write_text(
            json.dumps(frozen, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        assertion_count = sum(len(target["assertions"]) for target in candidate["targets"])
        return {
            "question": number,
            "status": "completed",
            "flash_seconds": elapsed,
            "target_count": len(candidate["targets"]),
            "assertion_count": assertion_count,
            "candidate_sha256": sha256_bytes(path.read_bytes()),
            "usage": frozen["usage"],
            "pipeline_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:  # noqa: BLE001 - bounded evaluation record
        return {
            "question": number,
            "status": "failed",
            "failure_type": type(exc).__name__,
            "message": str(exc)[:300],
            "pipeline_seconds": round(time.monotonic() - started, 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--visual-dir", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be from 1 to 4")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    private_dir = args.output_dir / "private-candidates"
    private_dir.mkdir(exist_ok=True)
    registry = year_eval.read_json(args.registry)
    flash = year_eval.load_model(registry, "deepseek-v4-flash-api")
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_question,
                number,
                args.source_root / entry_id,
                args.visual_dir,
                flash,
                private_dir,
            ): number
            for number, entry_id in year_eval.QUESTIONS
        }
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    rows.sort(key=lambda item: item["question"])
    summary = {
        "schema_version": 1,
        "generated_at": now(),
        "question_count": len(rows),
        "model": flash["id"],
        "thinking": "disabled",
        "official_answer_opened": False,
        "structural_success_count": sum(item["status"] == "completed" for item in rows),
        "flash_90s_pass_count": sum(
            item["status"] == "completed" and item.get("flash_seconds", 9999) <= 90 for item in rows
        ),
        "questions": rows,
    }
    (args.output_dir / "freeze-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
