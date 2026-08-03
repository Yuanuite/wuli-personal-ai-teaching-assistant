#!/usr/bin/env python3
"""Blind whole-paper competition evaluation for one direct API model.

This intentionally bypasses W3 decomposition, retrieval, verifier, Solver B,
adjudication, teaching-method and renderer gates.  The only model-side contract
is a compact, auditable solution for every sub-question.  Gold answers are not
accepted as an input and must be reviewed only after ``freeze.json`` exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
PROVIDERS = CONSOLE / "providers"
sys.path.insert(0, str(PROVIDERS))

import openai_compatible_agent_adapter as api_adapter  # noqa: E402


QUESTIONS = (
    (1, "20260730-question-01-p1-01627971"),
    (2, "20260730-question-02-p1-832dddbd"),
    (3, "20260730-question-03-p1-0df236a3"),
    (4, "20260730-question-04-complete-p1-f9a3416a"),
    (5, "20260730-question-05-p1-30f0cec9"),
    (6, "20260730-question-06-p1-4167f115"),
    (7, "20260730-question-07-p1-61942f3d"),
    (8, "20260730-question-08-complete-p1-3c56dc85"),
)


class TimedEvaluationError(RuntimeError):
    def __init__(self, elapsed_seconds: float, cause: Exception):
        super().__init__(str(cause))
        self.elapsed_seconds = elapsed_seconds
        self.error_type = type(cause).__name__


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_model(registry_path: Path, model_id: str) -> dict:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    for item in registry.get("models", []):
        if isinstance(item, dict) and str(item.get("id", "")) == model_id:
            api_key_env = str(
                item.get("api_key_env", "TEACHER_CONSOLE_AGENT_API_KEY")
            ).strip() or "TEACHER_CONSOLE_AGENT_API_KEY"
            api_key = str(item.get("api_key", "")).strip() or os.environ.get(
                api_key_env, ""
            ).strip()
            if str(item.get("provider", "")) != "openai-compatible":
                raise ValueError(f"model {model_id} is not openai-compatible")
            if not api_key:
                raise ValueError(f"model {model_id} has no configured API key")
            return {
                "base_url": str(item.get("base_url", "")).strip(),
                "model": str(item.get("model", "")).strip(),
                "api_key": api_key,
                "timeout": int(str(item.get("timeout_seconds", "120") or "120")),
            }
    raise ValueError(f"model not found: {model_id}")


def output_contract(profile: str) -> dict:
    if profile == "core":
        return {
            "name": "wuli.competition-direct-core.v1",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["completed", "unsupported"],
                    },
                    "message": {"type": "string", "maxLength": 160},
                    "results": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "part": {"type": "string", "maxLength": 80},
                                "final_answer": {
                                    "type": "string",
                                    "maxLength": 900,
                                },
                                "key_relations": {
                                    "type": "array",
                                    "minItems": 1,
                                    "maxItems": 4,
                                    "items": {
                                        "type": "string",
                                        "maxLength": 450,
                                    },
                                },
                                "confidence": {
                                    "type": "string",
                                    "enum": ["high", "medium", "low"],
                                },
                            },
                            "required": [
                                "part",
                                "final_answer",
                                "key_relations",
                                "confidence",
                            ],
                        },
                    },
                },
                "required": ["status", "message", "results"],
            },
            "instructions": (
                "这是竞赛核心答案盲测。覆盖全部小问；竞赛常见方法均可使用。先在内部完成"
                "计算、符号检查和必要修正，再一次性填写最终 JSON。每小问只写一个确定的"
                "final_answer 和至多四条决定性 key_relations。禁止展示草稿、自我争论、"
                "重复推导、候选答案或'可能/需要核对'等未决文本。不要引用标准答案。"
            ),
        }
    return {
        "name": "wuli.competition-direct-solution.v1",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "status": {"type": "string", "enum": ["completed", "unsupported"]},
                "message": {"type": "string", "maxLength": 300},
                "results": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "part": {"type": "string", "maxLength": 80},
                            "final_answer": {"type": "string", "maxLength": 1200},
                            "derivation": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 12,
                                "items": {"type": "string", "maxLength": 900},
                            },
                            "checks": {
                                "type": "array",
                                "maxItems": 4,
                                "items": {"type": "string", "maxLength": 300},
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                            },
                        },
                        "required": [
                            "part",
                            "final_answer",
                            "derivation",
                            "checks",
                            "confidence",
                        ],
                    },
                },
            },
            "required": ["status", "message", "results"],
        },
        "instructions": (
            "这是竞赛解题盲测。覆盖题目中的全部小问；允许使用官方竞赛常见方法，"
            "不受高中五步、微积分或教学排版门禁限制。每个小问给出最终结论和足以复核的"
            "关键推导，不复述题干，不输出隐藏思维过程。checks 只写量纲、极限、边界或"
            "代回检查的结论。不要猜测或引用标准答案。"
        ),
    }


def validate_candidate(candidate: dict, profile: str) -> None:
    if candidate.get("status") != "completed":
        raise ValueError(str(candidate.get("message", "unsupported")))
    results = candidate.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("candidate has no results")
    seen = set()
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("result is not an object")
        part = str(result.get("part", "")).strip()
        if not part or part in seen:
            raise ValueError("result part is missing or duplicated")
        seen.add(part)
        if not str(result.get("final_answer", "")).strip():
            raise ValueError(f"{part} has no final answer")
        evidence_field = "key_relations" if profile == "core" else "derivation"
        evidence = result.get(evidence_field)
        if not isinstance(evidence, list) or not any(
            str(item).strip() for item in evidence
        ):
            raise ValueError(f"{part} has no {evidence_field}")


def solve(
    question: int,
    entry_id: str,
    source_root: Path,
    model: dict,
    max_output_tokens: int,
    profile: str,
) -> dict:
    problem_path = source_root / entry_id / "problem.md"
    problem = problem_path.read_text(encoding="utf-8")
    task = {
        "prompt": (
            "独立求解下面这道物理竞赛题。不得访问外部答案或根据评测信息反推答案。\n\n"
            + problem
        ),
    }
    contract = output_contract(profile)
    instruction = api_adapter.build_structured_instruction(task, contract, "")
    options = api_adapter.request_options(
        model["base_url"],
        model["model"],
        {
            "TEACHER_CONSOLE_AGENT_API_MAX_OUTPUT_TOKENS": str(
                max_output_tokens
            )
        },
    )
    started = time.monotonic()
    try:
        candidate, payload = api_adapter.call_chat_completion(
            base=model["base_url"],
            model=model["model"],
            instruction=instruction,
            api_key=model["api_key"],
            timeout=model["timeout"],
            options=options,
        )
    except Exception as exc:
        raise TimedEvaluationError(
            round(time.monotonic() - started, 3), exc
        ) from exc
    elapsed = round(time.monotonic() - started, 3)
    validate_candidate(candidate, profile)
    return {
        "schema_version": 1,
        "question": question,
        "entry_id": entry_id,
        "model": str(payload.get("model") or model["model"]),
        "started_at": now(),
        "elapsed_seconds": elapsed,
        "usage": api_adapter.normalized_usage(payload),
        "problem_sha256": digest(problem_path),
        "candidate": candidate,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--model-id", default="deepseek-v4-flash-api")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--questions",
        default="1,2,3,4,5,6,7,8",
        help="comma-separated question numbers",
    )
    parser.add_argument("--max-output-tokens", type=int, default=10000)
    parser.add_argument(
        "--profile", choices=("core", "teaching"), default="core"
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be from 1 to 4")
    if not 256 <= args.max_output_tokens <= 16384:
        parser.error("--max-output-tokens must be from 256 to 16384")
    try:
        selected_numbers = {
            int(item.strip())
            for item in args.questions.split(",")
            if item.strip()
        }
    except ValueError:
        parser.error("--questions must contain integers")
    selected = [item for item in QUESTIONS if item[0] in selected_numbers]
    if not selected or {item[0] for item in selected} != selected_numbers:
        parser.error("--questions contains an unknown question")
    model = load_model(args.registry, args.model_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                solve,
                question,
                entry_id,
                args.source_root,
                model,
                args.max_output_tokens,
                args.profile,
            ): question
            for question, entry_id in selected
        }
        for future in as_completed(futures):
            question = futures[future]
            try:
                row = future.result()
                path = args.output_dir / f"q{question:02d}-candidate.json"
                path.write_text(
                    json.dumps(row, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                rows.append(
                    {
                        "question": question,
                        "status": "completed",
                        "elapsed_seconds": row["elapsed_seconds"],
                        "usage": row["usage"],
                        "candidate_sha256": digest(path),
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "question": question,
                        "status": "failed",
                        "elapsed_seconds": getattr(
                            exc, "elapsed_seconds", None
                        ),
                        "error_type": getattr(
                            exc, "error_type", type(exc).__name__
                        ),
                        "message": str(exc)[:500],
                    }
                )
    rows.sort(key=lambda item: item["question"])
    freeze = {
        "schema_version": 1,
        "frozen_at": now(),
        "model_id": args.model_id,
        "model": model["model"],
        "transport": "openai-compatible-direct-api",
        "gate_profile": f"competition-minimal-{args.profile}-v1",
        "gold_used_before_freeze": False,
        "knowledge_store_used": False,
        "checkpoint_used": False,
        "max_output_tokens": args.max_output_tokens,
        "questions": rows,
    }
    (args.output_dir / "freeze.json").write_text(
        json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "frozen",
                "completed": sum(row["status"] == "completed" for row in rows),
                "failed": sum(row["status"] == "failed" for row in rows),
                "freeze": str(args.output_dir / "freeze.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if all(row["status"] == "completed" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
