#!/usr/bin/env python3
"""Blind Flash physical-kernel ablation for the CPhO 2021 year set.

The remote request contains the reviewed problem and cached MiMo visual facts,
but never an official solution.  It asks for physical process modelling only:
no derivation, numerical calculation, or final answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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


PHYSICAL_KERNEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "unsupported"]},
        "message": {"type": "string", "maxLength": 300},
        "target_brief_digest": {"type": ["string", "null"]},
        "kernel": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "system_boundary": {"type": "string", "maxLength": 500},
                "state_variables": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 240},
                    "minItems": 1,
                    "maxItems": 12,
                },
                "process_stages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "maxLength": 20},
                            "state": {"type": "string", "maxLength": 300},
                            "event_or_transition": {"type": "string", "maxLength": 300},
                        },
                        "required": ["id", "state", "event_or_transition"],
                    },
                    "minItems": 1,
                    "maxItems": 10,
                },
                "interactions_and_directions": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 300},
                    "minItems": 1,
                    "maxItems": 12,
                },
                "governing_laws": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 300},
                    "minItems": 1,
                    "maxItems": 10,
                },
                "constraints_and_boundaries": {
                    "type": "array",
                    "items": {"type": "string", "maxLength": 300},
                    "minItems": 1,
                    "maxItems": 12,
                },
            },
            "required": [
                "system_boundary",
                "state_variables",
                "process_stages",
                "interactions_and_directions",
                "governing_laws",
                "constraints_and_boundaries",
            ],
        },
        "targets": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string", "maxLength": 20},
                    "decisive_physics": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 300},
                        "minItems": 1,
                        "maxItems": 4,
                    },
                    "risk_checks": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 240},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                },
                "required": ["id", "decisive_physics", "risk_checks"],
            },
            "minItems": 1,
            "maxItems": 12,
        },
    },
    "required": ["status", "message", "target_brief_digest", "kernel", "targets"],
}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalize(payload: Any, brief: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("physical kernel must be an object")
    allowed = {"status", "message", "target_brief_digest", "kernel", "targets"}
    if set(payload) != allowed:
        raise ValueError("physical kernel fields do not match contract")
    if payload.get("status") != "completed":
        raise ValueError("physical kernel is unsupported")
    if payload.get("target_brief_digest") != brief["digest"]:
        raise ValueError("target brief digest mismatch")
    if not isinstance(payload.get("kernel"), dict):
        raise ValueError("kernel must be an object")
    targets = payload.get("targets")
    if not isinstance(targets, list):
        raise ValueError("targets must be an array")
    expected = [item["id"] for item in brief["targets"]]
    actual = [str(item.get("id", "")) for item in targets if isinstance(item, dict)]
    if actual != expected:
        raise ValueError(f"target ids mismatch: expected={expected}; actual={actual}")
    forbidden = {"final_answer", "answer", "numerical_result", "solution"}
    if forbidden & set(payload.get("kernel", {})):
        raise ValueError("physical kernel contains a forbidden answer field")
    return payload


def solve_kernel(
    problem: str,
    visual: dict,
    config: dict,
    *,
    thinking: str,
) -> tuple[dict, dict, float]:
    brief = core_analysis.build_target_brief(
        problem,
        method_profile="olympiad_official",
        has_visual_facts=True,
    )
    target_ids = ", ".join(item["id"] for item in brief["targets"])
    contract = {
        "name": "wuli.physical-kernel-ablation.v1",
        "schema": PHYSICAL_KERNEL_SCHEMA,
        "instructions": (
            "只分析物理过程，不求解。禁止代数推导、数值计算和最终答案。"
            "先固定系统边界、状态变量、过程阶段、事件顺序、相互作用方向、参考系、"
            "适用定律、守恒与非守恒来源、稳定性、临界条件和分支。"
            "不能以只列定律名称代替物理判断；每个方向和符号必须说明参考对象。"
            f"targets 必须且只能按顺序覆盖 {target_ids}；target_brief_digest 必须原样返回 {brief['digest']}。"
            "decisive_physics 只写足以唯一约束后续推导的物理判断，risk_checks 写最可能出错且可复核的检查。"
            "若题意或视觉事实不足以建立唯一物理模型，返回 unsupported，不猜测。"
        ),
    }
    visual_facts = visual["visual_facts"]
    task = {
        "prompt": (
            "分析下面的官方物理竞赛题，但不要计算答案。\n\n题干：\n"
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
            "TEACHER_CONSOLE_AGENT_API_MAX_OUTPUT_TOKENS": "5000",
            "TEACHER_CONSOLE_AGENT_API_THINKING": thinking,
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
    *,
    thinking: str,
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
        kernel, response, elapsed = solve_kernel(
            problem,
            visual,
            config,
            thinking=thinking,
        )
        frozen = {
            "schema_version": 1,
            "question": number,
            "problem_sha256": digest_bytes(problem.encode("utf-8")),
            "visual_sha256": digest_bytes(visual_path.read_bytes()),
            "candidate": kernel,
            "flash_seconds": elapsed,
            "usage": api_adapter.normalized_usage(response),
        }
        frozen_path = private_dir / f"q{number:02d}.json"
        frozen_path.write_text(
            json.dumps(frozen, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "question": number,
            "status": "completed",
            "flash_seconds": elapsed,
            "target_count": len(kernel["targets"]),
            "candidate_sha256": digest_bytes(frozen_path.read_bytes()),
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
    parser.add_argument(
        "--thinking",
        choices=("enabled", "disabled"),
        default="disabled",
    )
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
                thinking=args.thinking,
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
        "thinking": args.thinking,
        "official_answer_opened": False,
        "structural_success_count": sum(item["status"] == "completed" for item in rows),
        "flash_90s_pass_count": sum(
            item["status"] == "completed" and item.get("flash_seconds", 9999) <= 90
            for item in rows
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
