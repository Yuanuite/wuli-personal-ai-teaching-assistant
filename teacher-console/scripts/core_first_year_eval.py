#!/usr/bin/env python3
"""Blind MiMo -> Flash core-first year evaluation.

This runner never opens an official solution. Frozen candidates can be
compared with local authority later, outside every remote-provider path.
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
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "teacher-console"
SKILL_SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
for path in (CONSOLE, SKILL_SCRIPTS, CONSOLE / "providers"):
    sys.path.insert(0, str(path))

import core_analysis  # noqa: E402
import openai_compatible_agent_adapter as api_adapter  # noqa: E402
import source_review  # noqa: E402
from visual_extraction import extract_visual_facts  # noqa: E402

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


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_model(registry: dict, model_id: str) -> dict:
    for raw in registry.get("models", []):
        if not isinstance(raw, dict) or raw.get("id") != model_id:
            continue
        if raw.get("provider") != "openai-compatible":
            raise ValueError(f"{model_id} is not openai-compatible")
        config = dict(raw)
        if not str(config.get("api_key", "")).strip():
            import os

            config["api_key"] = os.environ.get(str(config.get("api_key_env", "")), "")
        if not str(config.get("api_key", "")).strip():
            raise ValueError(f"{model_id} has no API key")
        return config
    raise ValueError(f"model not found: {model_id}")


def source_preflight(entry: Path) -> dict:
    record = read_json(entry / "record.json")
    stored = [str(item) for item in record.get("source", {}).get("stored_files", [])]
    image_paths = [
        entry / item for item in stored if (entry / item).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]
    missing = [str(path.relative_to(entry)) for path in image_paths if not path.is_file()]
    return {
        "stored_count": len(stored),
        "image_count": sum(path.is_file() for path in image_paths),
        "missing": missing,
        "download_needed": not image_paths or bool(missing),
    }


def visual_extract(entry: Path, config: dict) -> tuple[dict, float]:
    review = source_review.review_payload(entry)
    record = read_json(entry / "record.json")
    ocr = read_json(entry / "ocr.json")
    fingerprint = "sha256:" + source_review.input_digest(entry, record, ocr)

    def resolver(_trait: str, *, routing_tier: str = "auto") -> dict:
        return config

    started = time.monotonic()
    result = extract_visual_facts(
        review,
        fingerprint,
        model_resolver=resolver,
        expected_runtime_identity={"model_id": config["id"], "provider": config["provider"]},
        allow_remote=True,
        urlopen=urlopen,
        routing_tier="economy",
    )
    return result, round(time.monotonic() - started, 3)


def flash_solve(
    problem: str,
    visual: dict,
    config: dict,
    *,
    thinking: str = "default",
) -> tuple[dict, dict, float]:
    brief = core_analysis.build_target_brief(
        problem,
        method_profile="olympiad_official",
        has_visual_facts=True,
    )
    contract = core_analysis.output_contract(brief)
    task = {
        "prompt": (
            "独立求解下面的官方物理竞赛题。视觉事实仅用于补足原图信息。"
            "不得访问、猜测或引用标准答案。\n\n题干：\n"
            + problem
            + "\n\nMiMo 视觉事实：\n"
            + json.dumps(
                {
                    "printed_facts": visual["visual_facts"]["printed_facts"],
                    "diagram_facts": visual["visual_facts"]["diagram_facts"],
                    "uncertainties": visual["visual_facts"]["uncertainties"],
                },
                ensure_ascii=False,
            )
        )
    }
    instruction = api_adapter.build_structured_instruction(task, contract, "")
    # Normal compact cores use well below 2k tokens. Bound pathological
    # verbosity so malformed output fails fast instead of consuming the SLA.
    option_env = {"TEACHER_CONSOLE_AGENT_API_MAX_OUTPUT_TOKENS": "4000"}
    if thinking in {"enabled", "disabled"}:
        option_env["TEACHER_CONSOLE_AGENT_API_THINKING"] = thinking
    options = api_adapter.request_options(
        str(config["base_url"]),
        str(config["model"]),
        option_env,
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
    elapsed = round(time.monotonic() - started, 3)
    try:
        normalized = core_analysis.normalize_payload(candidate, brief)
    except ValueError as exc:
        actual_ids = (
            [str(item.get("id", "")) for item in candidate.get("targets", []) if isinstance(item, dict)]
            if isinstance(candidate, dict)
            else []
        )
        expected_ids = [item["id"] for item in brief["targets"]]
        raise ValueError(f"{exc}; expected_target_ids={expected_ids}; actual_target_ids={actual_ids}") from exc
    return normalized, response, elapsed


def run_question(
    number: int,
    entry: Path,
    mimo: dict,
    flash: dict,
    private_dir: Path,
    *,
    thinking: str = "default",
    refresh_visual: bool = False,
) -> dict:
    started = time.monotonic()
    preflight = source_preflight(entry)
    if preflight["download_needed"]:
        return {"question": number, "status": "source-missing", "source": preflight}
    phase = "visual"
    visual_seconds: float | None = None
    flash_seconds: float | None = None
    visual_fallback = False
    try:
        visual_cache = private_dir / f"q{number:02d}-visual.json"
        if visual_cache.is_file() and not refresh_visual:
            visual = read_json(visual_cache)
            visual_seconds = 0.0
        else:
            visual, visual_seconds = visual_extract(entry, mimo)
            visual_cache.write_text(
                json.dumps(visual, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if visual["gate_result"]["status"] != "passed":
            record = read_json(entry / "record.json")
            if record.get("source_review", {}).get("status") == "passed":
                visual_fallback = True
            else:
                phase = "visual-gate"
                raise ValueError(
                    "MiMo visual facts did not pass source gate and no prior approved source exists; "
                    f"uncertainty_count={len(visual['visual_facts']['uncertainties'])}"
                )
        problem = (entry / "problem.md").read_text(encoding="utf-8")
        phase = "flash"
        candidate, response, flash_seconds = flash_solve(problem, visual, flash, thinking=thinking)
        private = {
            "schema_version": 1,
            "question": number,
            "entry_id": entry.name,
            "problem_sha256": sha256_bytes(problem.encode("utf-8")),
            "visual": visual,
            "candidate": candidate,
            "usage": api_adapter.normalized_usage(response),
            "visual_seconds": visual_seconds,
            "flash_seconds": flash_seconds,
            "pipeline_seconds": round(time.monotonic() - started, 3),
        }
        path = private_dir / f"q{number:02d}.json"
        path.write_text(json.dumps(private, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "question": number,
            "status": "completed",
            "source": preflight,
            "visual_gate": visual["gate_result"]["status"],
            "visual_fallback": ("prior-human-source-review" if visual_fallback else None),
            "visual_uncertainty_count": len(visual["visual_facts"]["uncertainties"]),
            "visual_seconds": visual_seconds,
            "flash_seconds": flash_seconds,
            "pipeline_seconds": private["pipeline_seconds"],
            "candidate_sha256": sha256_bytes(path.read_bytes()),
            "target_count": len(candidate["targets"]),
            "usage": private["usage"],
        }
    except Exception as exc:  # noqa: BLE001 - evaluation records bounded failure
        return {
            "question": number,
            "status": "failed",
            "source": preflight,
            "failed_phase": phase,
            "visual_seconds": visual_seconds,
            "flash_seconds": flash_seconds,
            "failure_type": type(exc).__name__,
            "message": str(exc)[:300],
            "pipeline_seconds": round(time.monotonic() - started, 3),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--questions", default="1,2,3,4,5,6,7,8")
    parser.add_argument(
        "--thinking",
        choices=("default", "enabled", "disabled"),
        default="default",
    )
    parser.add_argument("--refresh-visual", action="store_true")
    parser.add_argument(
        "--freeze-only",
        action="store_true",
        help="run MiMo + Flash and freeze candidates without opening gold",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be from 1 to 4")
    try:
        selected_numbers = {int(item.strip()) for item in args.questions.split(",") if item.strip()}
    except ValueError:
        parser.error("--questions must contain integers")
    selected_questions = [item for item in QUESTIONS if item[0] in selected_numbers]
    if not selected_questions or {item[0] for item in selected_questions} != selected_numbers:
        parser.error("--questions contains an unknown question")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    private_dir = args.output_dir / "private-candidates"
    private_dir.mkdir(exist_ok=True)
    registry = read_json(args.registry)
    mimo = load_model(registry, "mimo-v2.5-flash")
    flash = load_model(registry, "deepseek-v4-flash-api")

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                run_question,
                number,
                args.source_root / entry_id,
                mimo,
                flash,
                private_dir,
                thinking=args.thinking,
                refresh_visual=args.refresh_visual,
            ): number
            for number, entry_id in selected_questions
        }
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(
                json.dumps(
                    {
                        k: row.get(k)
                        for k in (
                            "question",
                            "status",
                            "failed_phase",
                            "failure_type",
                            "message",
                            "visual_seconds",
                            "flash_seconds",
                            "pipeline_seconds",
                        )
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    rows.sort(key=lambda item: item["question"])
    freeze = {
        "schema_version": 1,
        "frozen_at": now(),
        "models": {"vision": mimo["id"], "solver": flash["id"]},
        "gold_used_before_freeze": False,
        "method_profile": "olympiad_official",
        "thinking": args.thinking,
        "questions": rows,
    }
    freeze_path = args.output_dir / "freeze.json"
    freeze_path.write_text(json.dumps(freeze, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not args.freeze_only:
        parser.error("this runner only freezes blind candidates; pass --freeze-only")
    if args.freeze_only:
        structural = {
            "schema_version": 1,
            "generated_at": now(),
            "question_count": len(selected_questions),
            "source_redownload_needed_count": sum(row["source"]["download_needed"] for row in rows),
            "mimo_visual_gate_pass_count": sum(row.get("visual_gate") == "passed" for row in rows),
            "structural_success_count": sum(row["status"] == "completed" for row in rows),
            "flash_90s_pass_count": sum(
                row.get("status") == "completed" and row.get("flash_seconds", 9999) <= 90 for row in rows
            ),
            "combined_90s_pass_count": sum(
                row.get("status") == "completed" and row.get("pipeline_seconds", 9999) <= 90 for row in rows
            ),
            "gold_opened": False,
            "questions": rows,
        }
        (args.output_dir / "freeze-summary.json").write_text(
            json.dumps(structural, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(structural, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
