#!/usr/bin/env python3
"""Structured-file Agent adapter for an authorized OpenAI-compatible endpoint."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

MAX_CONTEXT_CHARS = 300_000
DEFAULT_MAX_OUTPUT_TOKENS = 6_000
# Complex problems (many targets, truncated evidence, or large contracts) may
# need a wider completion budget; the work-tree authorizes up to 30_000 and
# forces thinking off so reasoning cannot starve the JSON body.
MAX_OUTPUT_TOKENS_COMPLEX = 30_000
COMPLEX_TARGET_THRESHOLD = 5
COMPACT_SOLUTION_PREFIX = "wuli.solution-reasoning.v2.1.solver-"
SUBSCRIPT_TRANSLATION = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
SOLUTION_CORE_FIELDS = (
    "status",
    "message",
    "targets",
    "option_verdicts",
    "blueprint_audit",
)
SOLUTION_INTERFACE_FIELDS = (
    "stage_results",
    "stage_interfaces",
    "stage_transitions",
)
ENTRY_CONTEXT = (
    "problem.md",
    "student-solution.md",
    "teacher-solution.md",
    "solution.md",
    "record.json",
    "physics-model.json",
    "visual-facts.json",
    "answer-revision-request.json",
    "visualization-request.json",
)
ROUTING_TIERS = {"auto", "economy", "expert"}


def is_loopback(raw: str) -> bool:
    return (urlparse(raw).hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}


# Marker line the Gateway parses from stderr to attach structured failure
# telemetry to the attempt without ever including the reasoning body.
FAILURE_ENVELOPE_MARKER = "WULI_AGENT_FAILURE_ENVELOPE:"


class _AdapterFailure(Exception):
    """Structured adapter failure carrying redacted telemetry."""

    def __init__(
        self,
        failure_type: str,
        *,
        message: str,
        usage: dict | None = None,
        finish_reason: str = "",
        content_chars: int | None = None,
        reasoning_chars: int | None = None,
        request_count: int = 1,
        request_preflight: dict | None = None,
        phase: str = "",
        stage_progress: list | None = None,
        timeout_layer: str = "",
    ):
        super().__init__(message)
        self.failure_type = failure_type
        self.message = message
        self.usage = {k: v for k, v in (usage or {}).items() if isinstance(v, int) and v >= 0}
        self.finish_reason = finish_reason
        self.content_chars = content_chars
        self.reasoning_chars = reasoning_chars
        self.request_count = request_count
        self.request_preflight = dict(request_preflight or {})
        self.phase = phase
        self.stage_progress = list(stage_progress or [])
        self.timeout_layer = timeout_layer


def _emit_failure_envelope(exc: _AdapterFailure) -> None:
    """Write the structured, redacted failure envelope to stderr.

    Contains finish_reason, usage, char counts, request count, phase progress
    and request preflight only — never the reasoning body, prompt, keys, or
    entry content.
    """
    envelope = {
        "failure_type": exc.failure_type,
        "finish_reason": exc.finish_reason,
        "usage": exc.usage,
        "content_chars": exc.content_chars,
        "reasoning_chars": exc.reasoning_chars,
        "request_count": exc.request_count,
        "request_preflight": exc.request_preflight,
        "phase": exc.phase,
        "stage_progress": exc.stage_progress,
        "timeout_layer": exc.timeout_layer,
        "message": str(exc.message)[:500],
    }
    print(
        f"{FAILURE_ENVELOPE_MARKER}{json.dumps(envelope, ensure_ascii=False)}",
        file=sys.stderr,
    )


def _urlerror_timeout_signature(reason: object) -> bool:
    """True when a URLError ``reason`` is a network timeout (A1.3).

    ``urllib`` may wrap ``socket.timeout``/``TimeoutError`` or a ``timed out``
    message inside ``urllib.error.URLError``; all forms are the same provider
    soft timeout.
    """
    if isinstance(reason, TimeoutError):
        return True
    text = str(reason).lower()
    return "timed out" in text or "timeout" in text


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
    candidate = text
    for _ in range(32):
        try:
            value = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            if exc.msg != "Expecting ',' delimiter":
                raise
            unexpected = exc.pos
            while unexpected < len(candidate) and candidate[unexpected].isspace():
                unexpected += 1
            if unexpected >= len(candidate) or candidate[unexpected] in {",", ":", "]", "}", '"'}:
                raise
            quote = exc.pos - 1
            while quote >= 0 and candidate[quote].isspace():
                quote -= 1
            if quote < 0 or candidate[quote] != '"':
                raise
            slash_count = 0
            cursor = quote - 1
            while cursor >= 0 and candidate[cursor] == "\\":
                slash_count += 1
                cursor -= 1
            if slash_count % 2:
                raise
            candidate = candidate[:quote] + '\\"' + candidate[quote + 1 :]
    else:
        raise ValueError("model response contains too many malformed string quotes")
    if not isinstance(value, dict):
        raise ValueError("model response is not a JSON object")
    return value


def select_model(task: dict, environ: dict[str, str] | None = None) -> tuple[str, str, str, str]:
    env = os.environ if environ is None else environ
    requested = str(task.get("routing_tier", "auto")).strip().lower() or "auto"
    if requested not in ROUTING_TIERS:
        raise ValueError("routing_tier must be auto, economy, or expert")
    config = task.get("model_config")
    if isinstance(config, dict) and str(config.get("provider", "")).strip() == "openai-compatible":
        direct_model = str(config.get("model", "")).strip()
        if direct_model:
            tier = str(config.get("model_tier", "selected")).strip() or "selected"
            return direct_model, tier, requested, ""
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
        "prompt_tokens": "prompt_tokens",
        "completion_tokens": "completion_tokens",
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "total_tokens": "total_tokens",
    }
    result = {}
    for source, target in aliases.items():
        value = raw.get(source)
        if isinstance(value, int) and value >= 0:
            result[target] = value
    if "total_tokens" not in result:
        parts = [
            result.get("prompt_tokens", result.get("input_tokens")),
            result.get("completion_tokens", result.get("output_tokens")),
        ]
        if all(isinstance(value, int) for value in parts):
            result["total_tokens"] = sum(parts)
    return result


def compact_solution_contracts(contract: dict) -> tuple[dict, dict] | None:
    """Split the large W3 Solver schema without weakening the final contract."""
    if not str(contract.get("name", "")).startswith(COMPACT_SOLUTION_PREFIX):
        return None
    schema = contract.get("schema")
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        raise ValueError("compact W3 solution contract requires object properties")
    missing = [field for field in (*SOLUTION_CORE_FIELDS, *SOLUTION_INTERFACE_FIELDS) if field not in properties]
    if missing:
        raise ValueError(f"compact W3 solution contract is missing fields: {missing}")

    def tighten(node: object, path: tuple[str, ...] = ()) -> object:
        if isinstance(node, list):
            return [tighten(item, path) for item in node]
        if not isinstance(node, dict):
            return node
        result = {key: tighten(value, (*path, str(key))) for key, value in node.items()}
        raw_type = result.get("type")
        types = set(raw_type) if isinstance(raw_type, list) else {raw_type}
        if "string" in types and "enum" not in result:
            field = next(
                (
                    item
                    for item in reversed(path)
                    if item
                    not in {
                        "properties",
                        "items",
                        "additionalProperties",
                    }
                ),
                "",
            )
            result["maxLength"] = 400 if field in {"final_answer", "result"} else 160
        if "array" in types:
            field = next(
                (item for item in reversed(path) if item not in {"properties", "items"}),
                "",
            )
            if field in {"supporting_relations", "conditions", "revisions"}:
                result["maxItems"] = min(int(result.get("maxItems", 4)), 4)
        if "object" in types and isinstance(result.get("additionalProperties"), dict):
            result["maxProperties"] = min(int(result.get("maxProperties", 24)), 24)
        return result

    def subset(fields: tuple[str, ...]) -> dict:
        schema_subset = {
            "type": "object",
            "additionalProperties": False,
            "properties": {field: deepcopy(properties[field]) for field in fields},
            "required": list(fields),
        }
        return tighten(schema_subset)

    core = {
        "name": f"{contract['name']}.core",
        "schema": subset(SOLUTION_CORE_FIELDS),
        "instructions": (
            "第一段只求解题目目标：输出最终结论、决定性关系、条件、选项判断和蓝图覆盖。"
            "不要输出 stage_results、stage_interfaces 或 stage_transitions。每个目标只保留一条最终答案"
            "和至多四条不重复的决定性等式；conditions 只保留影响答案成立的条件；"
            "不得输出思维过程、复述题干或展开代数演算。"
            "所有字符串内部禁止出现未转义的双引号；引用术语时使用单引号。"
        ),
    }
    interfaces = {
        "name": f"{contract['name']}.interfaces",
        "schema": subset(SOLUTION_INTERFACE_FIELDS),
        "instructions": (
            "第二段只把已完成的核心解投影为简短阶段结果、物理阶段接口与相邻阶段状态映射。"
            "不得修改核心结论，不得增加新的答案分支。状态值只写符号或短语，"
            "不得在接口字段重复求解过程。所有字符串内部禁止出现未转义的双引号。"
        ),
    }
    return core, interfaces


def merge_compact_solution(core: dict, interfaces: dict) -> dict:
    result = {field: deepcopy(core.get(field)) for field in SOLUTION_CORE_FIELDS}
    if result.get("status") == "unsupported":
        result.update({
            "stage_interfaces": None,
            "stage_transitions": None,
        })
        return result
    result.update({field: deepcopy(interfaces.get(field)) for field in SOLUTION_INTERFACE_FIELDS})

    def stable_state_key(raw: object) -> str:
        text = str(raw).strip().translate(SUBSCRIPT_TRANSLATION)
        parts = []
        used_hash = False
        for char in text:
            if char.isascii() and (char.isalnum() or char in "._-"):
                parts.append(char)
            elif char.isspace():
                parts.append("_")
            else:
                name = unicodedata.name(char, "")
                if name.startswith("GREEK "):
                    parts.append("u" + format(ord(char), "04x"))
                else:
                    used_hash = True
                    parts.append("_")
        key = re.sub(r"_+", "_", "".join(parts)).strip("_")
        if used_hash:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
            key = f"{key}_{digest}".strip("_") if key else f"state_{digest}"
        if not key or not key[0].isalpha():
            key = f"state_{key}" if key else "state"
        return key[:80]

    def stable_state_list(values: object) -> object:
        if not isinstance(values, list):
            return values
        return list(dict.fromkeys(stable_state_key(item) for item in values))

    for interface in result.get("stage_interfaces") or []:
        if not isinstance(interface, dict) or not isinstance(interface.get("directions"), dict):
            continue
        normalized_directions = {}
        for raw_key, value in interface["directions"].items():
            key = str(raw_key).strip()
            sign = key[:1] if key[:1] in {"+", "-"} else ""
            body = key[1:] if sign else key
            parts = []
            for char in body:
                if char.isascii() and (char.isalnum() or char in "._-"):
                    parts.append(char)
                    continue
                name = unicodedata.name(char, "")
                if name.startswith("GREEK "):
                    parts.append(name.split()[-1].lower())
                else:
                    parts.append("_")
            body = re.sub(r"_+", "_", "".join(parts)).strip("_")
            if not body or not body[0].isalpha():
                body = f"axis_{body}" if body else "axis"
            normalized_directions[(sign + body)[:32]] = value
        interface["directions"] = normalized_directions
        for field in ("entry_state", "exit_state"):
            state = interface.get(field)
            if isinstance(state, dict):
                interface[field] = {stable_state_key(key): value for key, value in state.items()}
        for field in ("required_entry_keys", "carried_state_keys"):
            interface[field] = stable_state_list(interface.get(field))
    # The interface contract describes the same boundary in two complementary
    # ways.  A key mapped from the previous stage is, by definition, carried
    # rather than introduced.  Canonicalize this redundant bookkeeping at the
    # transport boundary so it cannot invalidate an otherwise complete solve.
    for transition in result.get("stage_transitions") or []:
        if not isinstance(transition, dict):
            continue
        mapping = transition.get("state_mapping")
        if isinstance(mapping, list):
            normalized_mapping = []
            seen_targets = set()
            for item in mapping:
                if not isinstance(item, dict):
                    continue
                normalized = deepcopy(item)
                normalized["from_key"] = stable_state_key(normalized.get("from_key"))
                normalized["to_key"] = stable_state_key(normalized.get("to_key"))
                if normalized["to_key"] in seen_targets:
                    continue
                seen_targets.add(normalized["to_key"])
                normalized_mapping.append(normalized)
            transition["state_mapping"] = normalized_mapping
        mapped_keys = {
            stable_state_key(item.get("to_key"))
            for item in transition.get("state_mapping") or []
            if isinstance(item, dict) and str(item.get("to_key", "")).strip()
        }
        introduced = transition.get("introduced_entry_keys")
        if isinstance(introduced, list):
            transition["introduced_entry_keys"] = [
                item for item in stable_state_list(introduced) if item not in mapped_keys
            ]
    interfaces_by_id = {
        str(item.get("stage_id", "")): item
        for item in result.get("stage_interfaces") or []
        if isinstance(item, dict) and str(item.get("stage_id", ""))
    }
    ordered_ids = list(interfaces_by_id)
    transitions_by_pair = {
        (
            str(item.get("from_stage", "")),
            str(item.get("to_stage", "")),
        ): item
        for item in result.get("stage_transitions") or []
        if isinstance(item, dict)
    }
    canonical_transitions = []
    for from_stage, to_stage in zip(ordered_ids, ordered_ids[1:]):
        existing = transitions_by_pair.get((from_stage, to_stage))
        if existing is not None:
            canonical_transitions.append(existing)
            continue
        source = interfaces_by_id[from_stage]
        target = interfaces_by_id[to_stage]
        source_state = source.get("exit_state", {})
        target_state = target.get("entry_state", {})
        shared = [key for key, value in target_state.items() if key in source_state and source_state[key] == value]
        canonical_transitions.append({
            "from_stage": from_stage,
            "to_stage": to_stage,
            "event": f"{from_stage} to {to_stage} stage boundary",
            "state_mapping": [
                {
                    "from_key": key,
                    "to_key": key,
                    "transform": None,
                }
                for key in shared
            ],
            "introduced_entry_keys": [key for key in target_state if key not in shared],
            "coordinate_transform": (
                None
                if source.get("coordinate_frame") == target.get("coordinate_frame")
                else "re-express state in the next stage coordinate frame"
            ),
            "time_transform": (
                None
                if source.get("time_origin") == target.get("time_origin")
                else "reset the time origin at the next stage boundary"
            ),
            "direction_transform": (
                None
                if source.get("directions") == target.get("directions")
                else "re-express signed directions in the next stage frame"
            ),
        })
    result["stage_transitions"] = canonical_transitions
    return result


def task_is_complex(task: dict) -> bool:
    """Complex problems need a wider completion budget and thinking off.

    Signals: a large number of targets, truncated knowledge evidence, or an
    oversized output contract. The decision is written into the run report so
    it cannot silently raise cost ceilings.
    """
    profile = task.get("request_complexity")
    if isinstance(profile, dict):
        target_count = profile.get("target_count")
        if (
            isinstance(target_count, int)
            and not isinstance(target_count, bool)
            and target_count >= COMPLEX_TARGET_THRESHOLD
        ):
            return True
        if profile.get("evidence_truncated") is True:
            return True
        schema_chars = profile.get("contract_schema_chars")
        if isinstance(schema_chars, int) and not isinstance(schema_chars, bool) and schema_chars > 4_000:
            return True
    contract = task.get("output_contract")
    if isinstance(contract, dict):
        schema = contract.get("schema")
        if isinstance(schema, dict):
            schema_size = len(json.dumps(schema, ensure_ascii=False))
            if schema_size > 4_000:
                return True
    target_brief = task.get("context_payloads", {}).get(".agent-context/target-brief.json", {})
    if isinstance(target_brief, dict):
        targets = target_brief.get("targets") or target_brief.get("target_count")
        if isinstance(targets, int) and targets >= COMPLEX_TARGET_THRESHOLD:
            return True
        if isinstance(targets, list) and len(targets) >= COMPLEX_TARGET_THRESHOLD:
            return True
    evidence = task.get("context_payloads", {}).get(".agent-context/knowledge-evidence.json", {})
    if isinstance(evidence, dict):
        context_budget = evidence.get("context_budget")
        if evidence.get("truncated") is True or (
            isinstance(context_budget, dict) and context_budget.get("truncated") is True
        ):
            return True
    return False


def request_options(
    base: str, model: str, environ: dict[str, str] | None = None, *, complex_task: bool = False
) -> dict:
    env = os.environ if environ is None else environ
    try:
        max_tokens = int(
            env.get(
                "TEACHER_CONSOLE_AGENT_API_MAX_OUTPUT_TOKENS",
                str(DEFAULT_MAX_OUTPUT_TOKENS),
            )
        )
    except ValueError:
        max_tokens = DEFAULT_MAX_OUTPUT_TOKENS
    cap = MAX_OUTPUT_TOKENS_COMPLEX if complex_task else 16_384
    if complex_task and max_tokens < MAX_OUTPUT_TOKENS_COMPLEX:
        max_tokens = MAX_OUTPUT_TOKENS_COMPLEX
    options: dict = {
        "temperature": 0.1,
        "max_tokens": max(256, min(max_tokens, cap)),
        "response_format": {"type": "json_object"},
    }
    thinking = env.get("TEACHER_CONSOLE_AGENT_API_THINKING", "").strip().lower()
    if complex_task:
        # Reasoning must not starve the JSON body of a complex problem.
        options["thinking"] = {"type": "disabled"}
    elif thinking in {"enabled", "disabled"}:
        options["thinking"] = {"type": thinking}
    return options


def build_structured_instruction(task: dict, contract: dict, context: str) -> str:
    schema = json.dumps(contract.get("schema", {}), ensure_ascii=False)
    contract_instructions = str(contract.get("instructions", "")).strip()
    return (
        "You are a scoped teaching-content worker. Return exactly one JSON object and no prose. "
        f"The JSON must satisfy this schema: {schema}. {contract_instructions} "
        "Never approve, publish, deliver, or alter review records. "
        "If the evidence is insufficient, return status=unsupported with a concise message.\n\n"
        f"TASK:\n{task['prompt']}\n\nCURRENT ENTRY CONTEXT:\n{context}"
    )


def call_chat_completion(
    *,
    base: str,
    model: str,
    instruction: str,
    api_key: str,
    timeout: int,
    options: dict,
) -> tuple[dict, dict]:
    body = json.dumps(
        {
            "model": model,
            **options,
            "messages": [
                {
                    "role": "system",
                    "content": ("Follow the structured JSON contract exactly. The response must be valid JSON."),
                },
                {"role": "user", "content": instruction},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(endpoint(base), data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise _AdapterFailure(
            "provider_timeout",
            message=f"request timed out after {timeout}s (HTTP soft deadline)",
            finish_reason="timeout",
            timeout_layer="http_soft",
        ) from exc
    choice = payload["choices"][0]
    if choice.get("finish_reason") == "length":
        message = choice.get("message", {})
        content = message.get("content", "") if isinstance(message, dict) else ""
        reasoning = message.get("reasoning_content", "") if isinstance(message, dict) else ""
        usage = normalized_usage(payload)
        raise _AdapterFailure(
            "output_truncated",
            message=(
                "model response reached max_tokens before JSON completed; "
                f"content_chars={len(content) if isinstance(content, str) else 0}; "
                f"reasoning_chars={len(reasoning) if isinstance(reasoning, str) else 0}; "
                f"completion_tokens={usage.get('completion_tokens', usage.get('output_tokens', 0))}"
            ),
            usage=usage,
            finish_reason="length",
            content_chars=len(content) if isinstance(content, str) else 0,
            reasoning_chars=len(reasoning) if isinstance(reasoning, str) else 0,
        )
    content = choice["message"].get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model response content is empty")
    return parse_content(content), payload


def add_usage(total: dict, payload: dict) -> dict:
    current = normalized_usage(payload)
    for key, value in current.items():
        if key == "total_tokens":
            continue
        total[key] = total.get(key, 0) + value
    prompt = total.get("prompt_tokens", total.get("input_tokens"))
    completion = total.get("completion_tokens", total.get("output_tokens"))
    if isinstance(prompt, int) and isinstance(completion, int):
        total["total_tokens"] = prompt + completion
    return total


def main() -> int:
    task = json.load(sys.stdin)
    base = os.environ.get("TEACHER_CONSOLE_AGENT_API_BASE_URL", "").strip()
    if not base or not os.environ.get("TEACHER_CONSOLE_AGENT_API_MODEL", "").strip():
        raise SystemExit("TEACHER_CONSOLE_AGENT_API_BASE_URL and TEACHER_CONSOLE_AGENT_API_MODEL are required")
    model, model_tier, requested_tier, routing_notice = select_model(task)
    if not is_loopback(base) and task.get("allow_remote") is not True:
        raise SystemExit("remote Agent API requires project privacy allow_remote_agent")

    contract = task.get("output_contract")
    context = load_context(task)
    if isinstance(contract, dict):
        instruction = build_structured_instruction(task, contract, context)
    else:
        allowed = json.dumps(task.get("allowed_paths", []), ensure_ascii=False)
        instruction = (
            "You are a scoped teaching-content worker. Return exactly one JSON object and no prose. "
            'Schema: {"status":"completed|unsupported","message":"...",'
            '"files":[{"path":"relative/path","content":"complete UTF-8 content"}]}. '
            f"You may propose only these paths: {allowed}. Never approve, publish, deliver, or alter review records. "
            "Return complete replacement contents, not diffs. "
            "If tools or evidence are insufficient, return unsupported with no files.\n\n"
            f"TASK:\n{task['prompt']}\n\nCURRENT ENTRY CONTEXT:\n{context}"
        )
    api_key = os.environ.get("TEACHER_CONSOLE_AGENT_API_KEY", "").strip()
    timeout = int(os.environ.get("TEACHER_CONSOLE_AGENT_API_TIMEOUT_SECONDS", "300"))
    options = request_options(base, model, complex_task=task_is_complex(task))
    if task_is_complex(task):
        result_preflight = {
            "request_preflight": {
                "mode": "complex-budget",
                "max_output_tokens": options.get("max_tokens"),
                "thinking": options.get("thinking", {}).get("type", "upstream-default"),
                "reason": "complex problem: wide budget + thinking off to protect the JSON body",
            }
        }
    else:
        result_preflight = {}
    deadline_budget = task.get("deadline_budget") if isinstance(task.get("deadline_budget"), dict) else {}
    task_deadline = float(deadline_budget.get("task_deadline") or 0)
    started_at_epoch = time.monotonic()
    stage_progress: list[dict] = []

    def record_stage(phase: str, started_at: float, payload: dict, request_count: int = 1) -> None:
        choice = (payload.get("choices") or [{}])[0] if isinstance(payload, dict) else {}
        usage_stage = normalized_usage(payload) if isinstance(payload, dict) else {}
        remaining = (
            round(max(0.0, task_deadline - (time.monotonic() - started_at_epoch)), 3) if task_deadline > 0 else None
        )
        stage_progress.append({
            "phase": phase,
            "started_at": started_at,
            "duration_seconds": round(time.monotonic() - started_at, 3),
            "request_count": request_count,
            "finish_reason": str(choice.get("finish_reason", "")) if isinstance(choice, dict) else "",
            "usage": usage_stage,
            "remaining_deadline_seconds": remaining,
        })

    try:
        parts = compact_solution_contracts(contract) if isinstance(contract, dict) else None
        usage: dict = {}
        if parts is None:
            phase_started = time.monotonic()
            result, payload = call_chat_completion(
                base=base,
                model=model,
                instruction=instruction,
                api_key=api_key,
                timeout=timeout,
                options=options,
            )
            record_stage("single", phase_started, payload)
            add_usage(usage, payload)
        else:
            core_contract, interface_contract = parts
            core_started = time.monotonic()
            try:
                core, core_payload = call_chat_completion(
                    base=base,
                    model=model,
                    instruction=build_structured_instruction(task, core_contract, context),
                    api_key=api_key,
                    timeout=timeout,
                    options=options,
                )
            except _AdapterFailure as exc:
                exc.phase = "compact-core"
                exc.stage_progress = list(stage_progress)
                raise
            except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"compact core call failed: {exc}") from exc
            record_stage("compact-core", core_started, core_payload)
            add_usage(usage, core_payload)
            payload = core_payload
            if str(core.get("status", "")).strip().lower() == "unsupported":
                result = merge_compact_solution(core, {})
            else:
                interface_context = (
                    f"{context}\n\n--- VERIFIED CORE CANDIDATE (DO NOT ALTER) ---\n"
                    f"{json.dumps(core, ensure_ascii=False)}\n"
                )
                interface_started = time.monotonic()
                try:
                    interfaces, interface_payload = call_chat_completion(
                        base=base,
                        model=model,
                        instruction=build_structured_instruction(task, interface_contract, interface_context),
                        api_key=api_key,
                        timeout=timeout,
                        options=options,
                    )
                except _AdapterFailure as exc:
                    exc.phase = "compact-interface"
                    exc.stage_progress = list(stage_progress)
                    raise
                except (
                    KeyError,
                    IndexError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ) as exc:
                    raise ValueError(f"compact interface call failed: {exc}") from exc
                record_stage("compact-interface", interface_started, interface_payload)
                add_usage(usage, interface_payload)
                payload = interface_payload
                result = merge_compact_solution(core, interfaces)
        result["model"] = str(payload.get("model") or model)
        result["model_tier"] = model_tier
        result["requested_tier"] = requested_tier
        result["usage"] = usage
        result.update(result_preflight)
        if stage_progress:
            result["stage_progress"] = stage_progress
        if routing_notice:
            result["routing_notice"] = routing_notice
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except _AdapterFailure as exc:
        if not exc.request_preflight:
            exc.request_preflight = dict(result_preflight.get("request_preflight", {}))
        if not exc.phase:
            exc.phase = "single"
        exc.stage_progress = list(stage_progress) or exc.stage_progress
        _emit_failure_envelope(exc)
        print(f"OpenAI-compatible Agent adapter failed: {exc}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        _emit_failure_envelope(
            _AdapterFailure(
                "provider_execution_failed",
                message=f"HTTP {exc.code}",
                usage=usage if "usage" in dir() else {},
                request_preflight=result_preflight.get("request_preflight", {}),
            )
        )
        print(
            f"OpenAI-compatible Agent adapter failed: HTTP {exc.code}: {detail}",
            file=sys.stderr,
        )
        return 1
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        # A1.3 (w3-w3r work-tree): a URLError wrapping a socket/connect timeout
        # is still a provider soft timeout and must produce the same structured
        # envelope as the direct TimeoutError path.
        failure_type = "provider_execution_failed"
        finish_reason = ""
        timeout_layer = ""
        if isinstance(exc, urllib.error.URLError):
            reason = exc.reason
            if _urlerror_timeout_signature(reason):
                failure_type = "provider_timeout"
                finish_reason = "timeout"
                timeout_layer = "http_soft"
        _emit_failure_envelope(
            _AdapterFailure(
                failure_type,
                message=str(exc),
                finish_reason=finish_reason,
                timeout_layer=timeout_layer,
                usage=usage if "usage" in dir() else {},
                request_preflight=result_preflight.get("request_preflight", {}),
            )
        )
        print(f"OpenAI-compatible Agent adapter failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
