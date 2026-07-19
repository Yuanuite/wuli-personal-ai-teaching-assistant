#!/usr/bin/env python3
"""Structured-file Agent adapter for an authorized OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


MAX_CONTEXT_CHARS = 300_000
ENTRY_CONTEXT = (
    "problem.md",
    "student-solution.md",
    "teacher-solution.md",
    "solution.md",
    "record.json",
    "physics-model.json",
    "answer-revision-request.json",
    "visualization-request.json",
)
ROUTING_TIERS = {"auto", "economy", "expert"}


def is_loopback(raw: str) -> bool:
    return (urlparse(raw).hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}


def endpoint(base: str) -> str:
    clean = base.rstrip("/")
    return clean if clean.endswith("/chat/completions") else f"{clean}/chat/completions"


def load_context(task: dict) -> str:
    entry = Path(task["entry_dir"]).resolve()
    chunks = []
    total = 0
    paths = [entry / name for name in ENTRY_CONTEXT]
    paths.extend(path for path in (entry / "assets").glob("*.svg") if path.is_file() and not path.is_symlink())
    paths.extend(path for path in (entry / ".agent-context").glob("*") if path.is_file() and not path.is_symlink())
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        block = f"\n--- FILE {path.relative_to(entry)} ---\n{text}\n"
        if total + len(block) > MAX_CONTEXT_CHARS:
            break
        chunks.append(block)
        total += len(block)
    return "".join(chunks)


def parse_content(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("model response is not a JSON object")
    return value


def select_model(task: dict, environ: dict[str, str] | None = None) -> tuple[str, str, str, str]:
    env = os.environ if environ is None else environ
    requested = str(task.get("routing_tier", "auto")).strip().lower() or "auto"
    if requested not in ROUTING_TIERS:
        raise ValueError("routing_tier must be auto, economy, or expert")
    standard = env.get("TEACHER_CONSOLE_AGENT_API_MODEL", "").strip()
    economy = env.get("TEACHER_CONSOLE_AGENT_API_ECONOMY_MODEL", "").strip()
    expert = env.get("TEACHER_CONSOLE_AGENT_API_EXPERT_MODEL", "").strip()
    if not standard:
        raise ValueError("TEACHER_CONSOLE_AGENT_API_MODEL is required")
    effective = requested
    if requested == "auto":
        effective = "expert" if task.get("kind") == "visualization.model" and expert else "standard"
    selected = {"economy": economy, "expert": expert, "standard": standard}.get(effective, "")
    if selected:
        return selected, effective, requested, ""
    return standard, "standard", requested, f"未配置{effective}模型，已降级为标准模型"


def normalized_usage(payload: dict) -> dict:
    raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    aliases = {
        "prompt_tokens": "prompt_tokens", "completion_tokens": "completion_tokens",
        "input_tokens": "input_tokens", "output_tokens": "output_tokens", "total_tokens": "total_tokens",
    }
    result = {}
    for source, target in aliases.items():
        value = raw.get(source)
        if isinstance(value, int) and value >= 0:
            result[target] = value
    if "total_tokens" not in result:
        parts = [result.get("prompt_tokens", result.get("input_tokens")), result.get("completion_tokens", result.get("output_tokens"))]
        if all(isinstance(value, int) for value in parts):
            result["total_tokens"] = sum(parts)
    return result


def main() -> int:
    task = json.load(sys.stdin)
    base = os.environ.get("TEACHER_CONSOLE_AGENT_API_BASE_URL", "").strip()
    if not base or not os.environ.get("TEACHER_CONSOLE_AGENT_API_MODEL", "").strip():
        raise SystemExit("TEACHER_CONSOLE_AGENT_API_BASE_URL and TEACHER_CONSOLE_AGENT_API_MODEL are required")
    model, model_tier, requested_tier, routing_notice = select_model(task)
    if not is_loopback(base):
        if os.environ.get("TEACHER_CONSOLE_AGENT_ALLOW_REMOTE") != "true" or task.get("allow_remote") is not True:
            raise SystemExit("remote Agent API requires both project and environment privacy gates")

    allowed = json.dumps(task.get("allowed_paths", []), ensure_ascii=False)
    instruction = (
        "You are a scoped teaching-content worker. Return exactly one JSON object and no prose. "
        "Schema: {\"status\":\"completed|unsupported\",\"message\":\"...\","
        "\"files\":[{\"path\":\"relative/path\",\"content\":\"complete UTF-8 content\"}]}. "
        f"You may propose only these paths: {allowed}. Never approve, publish, deliver, or alter review records. "
        "Return complete replacement contents, not diffs. If tools or evidence are insufficient, return unsupported with no files.\n\n"
        f"TASK:\n{task['prompt']}\n\nCURRENT ENTRY CONTEXT:\n{load_context(task)}"
    )
    body = json.dumps({
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": "Follow the structured JSON contract exactly."},
            {"role": "user", "content": instruction},
        ],
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("TEACHER_CONSOLE_AGENT_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint(base), data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=int(os.environ.get("TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS", "300"))) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        result = parse_content(content)
        result["model"] = str(payload.get("model") or model)
        result["model_tier"] = model_tier
        result["requested_tier"] = requested_tier
        result["usage"] = normalized_usage(payload)
        if routing_notice:
            result["routing_notice"] = routing_notice
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"OpenAI-compatible Agent adapter failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
