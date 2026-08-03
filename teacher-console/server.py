#!/usr/bin/env python3
"""Local-only teacher console for the student error-library lifecycle."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

CONSOLE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CONSOLE_DIR.parent
STATIC_DIR = CONSOLE_DIR / "static"
LIBRARY = PROJECT_ROOT / "student-error-library"
UPLOADS = PROJECT_ROOT / "error-collection"
PUBLIC_SITE = PROJECT_ROOT / "student-site"
SKILL_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
MODEL_REGISTRY_PATH = LIBRARY / "config" / "model-registry.json"
W3_ROUTING_CONFIG_PATH = LIBRARY / "config" / "w3-production-routing.json"
W3R_ROUTING_CONFIG_NAME = "w3r-production-routing.json"
CORE_ROUTING_CONFIG_NAME = "analysis-production-routing.json"
sys.path.insert(0, str(SKILL_SCRIPTS))
sys.path.insert(0, str(CONSOLE_DIR))

import analysis_artifacts  # noqa: E402
import analysis_routing  # noqa: E402
import candidate_archive  # noqa: E402
import core_analysis  # noqa: E402
import correctness_policy  # noqa: E402
import difficulty_assessment  # noqa: E402
import evaluator  # noqa: E402
import kb  # noqa: E402
import physics_diagram  # noqa: E402
import problem_decomposition  # noqa: E402
import process_uploads  # noqa: E402
import public_site  # noqa: E402
import solution_reasoning  # noqa: E402
import solution_verification  # noqa: E402
import svg_collaboration  # noqa: E402
import teacher_feedback  # noqa: E402
import teaching_method_policy  # noqa: E402
import w3_pipeline  # noqa: E402
from agent_gateway import AgentGateway  # noqa: E402
from agent_jobs import AgentJobManager  # noqa: E402
from failure_intelligence import run_with_failure_repair  # noqa: E402
from log import TraceContext, logger
from log import configure as configure_logging  # noqa: E402
from model_registry import (  # noqa: E402
    analysis_qualification_public,
    model_config_for_task,
    model_registry_public,
    model_registry_settings,
    normalize_model_id,
    resolve_model_id_for_task,
    save_model_registry_settings,
    update_model_probe_result,
)
from route_snapshot import (  # noqa: E402
    build_route_snapshot,
    route_config_digest,
    route_snapshot_is_stale,
    route_snapshot_summary,
)
from runtime_environment import (  # noqa: E402
    classify_runtime_probe,
    resolved_environment,
    runtime_identity,
    runtime_identity_is_stale,
    runtime_settings_public,
    save_runtime_settings,
    update_runtime_probe_result,
)
from visual_application import run_visual_extract  # noqa: E402

CONSOLE_SCRIPTS = CONSOLE_DIR / "scripts"
sys.path.insert(0, str(CONSOLE_SCRIPTS))
import retrieval_benchmark  # noqa: E402

MAX_UPLOAD = 30 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
ALLOWED_UPLOADS = kb.SUPPORTED_EXTENSIONS
FOLDER_LOCK = threading.RLock()
# Start-time identity snapshot: /api/health compares it against the live
# identity so the UI can warn that the server must be restarted after disk
# changes without auto-restarting or blocking anything.
SERVER_RUNTIME_IDENTITY_SNAPSHOT = runtime_identity(
    project_root=PROJECT_ROOT, library=LIBRARY
)
LIBRARY_INDEX_LOCK = threading.RLock()
VISUALIZATION_LOCKS: dict[str, threading.RLock] = {}
VISUALIZATION_LOCKS_GUARD = threading.Lock()
PUBLICATION_LOCK = threading.RLock()
AGENT_GATEWAY = AgentGateway(environment_resolver=lambda: resolved_environment(LIBRARY))
_JOB_MANAGER: AgentJobManager | None = None
_JOB_MANAGER_LOCK = threading.Lock()


class IndexRebuildDebouncer:
    """Coalesce bursty source.clean completions into one library rebuild."""

    def __init__(self, delay_seconds: float):
        self.delay_seconds = max(0.05, float(delay_seconds))
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending = False

    def schedule(self) -> None:
        with self._lock:
            self._pending = True
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.delay_seconds, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            timer, pending = self._timer, self._pending
            self._timer = None
            self._pending = False
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()
        if pending:
            with LIBRARY_INDEX_LOCK:
                kb.rebuild_index(LIBRARY)


def _source_clean_debounce_seconds() -> float:
    try:
        return float(os.environ.get("TEACHER_CONSOLE_SOURCE_CLEAN_INDEX_DEBOUNCE_SECONDS", "2"))
    except ValueError:
        return 2.0


SOURCE_CLEAN_INDEX_DEBOUNCER = IndexRebuildDebouncer(_source_clean_debounce_seconds())
SUPPORTED_SIMULATOR_MODEL_TYPES = [
    "concentric-radial-multi-field",
    "opposite-circular-magnetic",
    "electric-to-bounded-magnetic",
    "planar-magnetic-multi-particle",
    "piecewise-field-particle-2d",
    "piecewise-field-particle-3d",
]
DELIVERY_CATALOG = {
    "student-package.zip": {
        "kind": "student-package",
        "purpose": "推荐发送给学生：答案、PDF、解释图和可视化（如有）已打包。",
        "recommended": True,
        "order": 1,
    },
    "带答案错题.pdf": {
        "kind": "pdf",
        "purpose": "适合直接阅读、打印或发送给学生，版式固定。",
        "recommended": True,
        "order": 2,
    },
    "带答案错题.md": {
        "kind": "markdown",
        "purpose": "适合教师继续编辑，或交给 Claude Code 等平台再处理。",
        "recommended": False,
        "order": 3,
    },
    "simulation/physics-simulator.html": {
        "kind": "simulator-html",
        "purpose": "课堂本机直接打开的离线互动仿真。",
        "recommended": False,
        "order": 4,
    },
    "simulation/physics-simulator.zip": {
        "kind": "simulator-package",
        "purpose": "单独发送可视化时使用；学生解压后打开 HTML。",
        "recommended": False,
        "order": 5,
    },
}
def _sanitize_output(text: str, max_len: int = 2000) -> str:
    """Truncate and sanitize agent stdout/stderr to avoid leaking paths or config."""
    truncated = text.strip()[:max_len]
    if len(text.strip()) > max_len:
        truncated += "\n… (truncated)"
    return truncated


PROTECTED_RECORD_FIELDS = {
    "schema_version",
    "id",
    "kind",
    "status",
    "answer_status",
    "created_at",
    "updated_at",
    "library_folder",
    "source",
    "ocr",
    "source_review",
    "answer_review",
    "visualization_review",
    "generated_from",
    "review",
}


def job_manager() -> AgentJobManager:
    global _JOB_MANAGER
    with _JOB_MANAGER_LOCK:
        if _JOB_MANAGER is None:
            _JOB_MANAGER = AgentJobManager(LIBRARY / ".cache" / "agent-jobs")
        return _JOB_MANAGER


def agent_health(*, force: bool = False) -> dict:
    result = json.loads(json.dumps(AGENT_GATEWAY.health(force=force)))
    if not remote_agent_allowed():
        for provider in result.get("providers", []):
            if provider.get("data_locality") == "remote":
                provider["available"] = False
                provider["reason"] = "项目 privacy.allow_remote_agent 尚未授权"
        available = [item for item in result.get("providers", []) if item.get("available")]
        if result.get("mode") == "auto":
            priority = {
                name: index
                for index, name in enumerate(("adapter", "openai-compatible", "legacy-command", "codex", "claude"))
            }
            available.sort(key=lambda item: priority.get(item.get("name"), 99))
            result["selected"] = available[0]["name"] if available else None
        elif not any(item.get("name") == result.get("selected") for item in available):
            result["selected"] = None
        result["available"] = result.get("selected") is not None
        if not result["available"]:
            result["reason"] = "没有通过项目隐私门禁的 provider"
    result["model_registry"] = model_registry_public()
    return result


def agent_available() -> bool:
    return bool(agent_health()["available"])


ROUTING_TIERS = {"auto", "economy", "expert"}


def normalize_routing_tier(value) -> str:
    tier = str(value or "auto").strip().lower()
    if tier not in ROUTING_TIERS:
        raise ValueError("routing_tier must be auto, economy, or expert")
    return tier


def source_clean_routing_tier(routing_tier: str | None) -> str:
    """题干整理任务默认走 economy；只有显式指定 expert 才用深度模型。"""
    tier = str(routing_tier or "auto").strip().lower()
    if tier == "expert":
        return "expert"
    return "economy"


def gateway_routing_fields(gateway: dict) -> dict:
    keys = (
        "routing_tier",
        "requested_tier",
        "model_tier",
        "model",
        "model_id",
        "model_display_name",
        "usage",
        "routing_notice",
        "failure_type",
        "evidence_context",
        "failure_repair",
        "budget_guard",
        "deadline_budget",
        "deadline_budget_problems",
        "timeout_summary",
        "outcome",
        "materialization",
        "resumed_from_checkpoint",
    )
    return {key: gateway[key] for key in keys if key in gateway}


def _sha256_fingerprint(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
        return f"sha256:{value}"
    if isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return value
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _stage_svg_provenance(
    staging: Path,
    *,
    model_config: dict | None,
    generation_fingerprint: str,
) -> dict | None:
    """Bind deterministic SVG to reviewed MiMo facts and the configured solver route."""
    facts_path = staging / "visual-facts.json"
    svg_path = staging / analysis_artifacts.EXPLANATION_PATH
    if not facts_path.is_file() or not svg_path.is_file():
        return None
    if not isinstance(model_config, dict):
        raise ValueError("SVG provenance requires a resolved generation model")
    identity = {
        "model_id": str(model_config.get("id", "")).strip(),
        "provider": str(model_config.get("provider", "")).strip(),
    }
    trace = {**identity, "generation_fingerprint": _sha256_fingerprint(generation_fingerprint)}
    provenance = svg_collaboration.validate_and_bind_svg(
        svg_path.read_text(encoding="utf-8"),
        kb.load_json(facts_path, {}),
        trace,
        identity,
    )
    kb.write_json(staging / "svg-provenance.json", provenance)
    return provenance


def run_agent_gateway(
    entry: Path,
    task: dict,
    validator,
    *,
    materializer=None,
    bounded_failure_repair: bool = True,
) -> dict:
    """Run one scoped task through the Gateway and the bounded repair policy."""
    if materializer is None:
        run_once = AGENT_GATEWAY.run
    else:
        def run_once(current_task, current_validator):
            return AGENT_GATEWAY.run(
                current_task,
                current_validator,
                materializer=materializer,
            )
    result = (
        run_with_failure_repair(
            task,
            validator,
            library=entry.parent.parent,
            run_once=run_once,
        )
        if bounded_failure_repair
        else run_once(task, validator)
    )
    from agent_outcome import build_agent_request_outcome

    result["outcome"] = build_agent_request_outcome(result)
    return result


def archive_agent_result(
    entry: Path,
    task_type: str,
    request: dict,
    gateway: dict,
    *,
    summary: str,
) -> dict:
    """Persist compact terminal Agent telemetry without raw prompts or process output."""
    library = entry.parent.parent
    status = str(gateway.get("status", "failed"))
    attempts = []
    for attempt in gateway.get("attempts", []) if isinstance(gateway.get("attempts"), list) else []:
        if not isinstance(attempt, dict):
            continue
        attempts.append({
            key: attempt[key]
            for key in ("provider", "status", "failure_type", "duration_seconds")
            if key in attempt
        })
    compact_result = {
        key: gateway[key]
        for key in (
            "status",
            "provider",
            "failure_type",
            "changed_files",
            "unauthorized_changes",
            "validation_errors",
            "failure_repair",
            "evidence_context",
            "budget_guard",
            "outcome",
        )
        if key in gateway
    }
    compact_result["attempts"] = attempts
    compact_request = {
        key: request[key]
        for key in ("routing_tier", "model_id", "model_display_name")
        if key in request
    }
    feedback_text = str(request.get("note") or request.get("message") or "")
    links = {}
    if request.get("feedback_event_id"):
        links["feedback_event_id"] = request["feedback_event_id"]
    try:
        evaluation = evaluator.evaluate_entry(library, entry.name, write=True)
        evaluation_summary = {
            key: evaluation.get(key)
            for key in (
                "status",
                "scores",
                "teacher_review_required",
                "failure_reasons",
                "warning_reasons",
            )
        }
    except Exception as exc:  # noqa: BLE001 - telemetry must not break the lifecycle
        logger.warning("agent archive evaluation failed entry=%s error=%s", entry.name, exc)
        evaluation_summary = {}
    try:
        return candidate_archive.append_event(
            library,
            entry,
            task_type=task_type,
            actor="agent",
            event_type="agent-result",
            status=status,
            summary=summary,
            request=compact_request,
            result=compact_result,
            evaluation=evaluation_summary,
            changed_files=gateway.get("changed_files", []),
            feedback={
                "categories": teacher_feedback.feedback_categories(
                    feedback_text, changed_files=gateway.get("changed_files", [])
                )
            }
            if feedback_text
            else {},
            links=links,
            failure_reasons=[]
            if status == "completed"
            else [str(gateway.get("message") or gateway.get("failure_type") or "Agent 任务失败")],
        )
    except Exception as exc:  # noqa: BLE001 - archive is observability, not a lifecycle gate
        logger.warning("agent archive append failed entry=%s error=%s", entry.name, exc)
        return {"status": "archive-error", "error": str(exc)}


def archive_claim_evidence_shadow(entry: Path, request: dict) -> dict:
    """Archive only compact correctness telemetry, never Claim prose or paths."""
    report = request.get("report", {}) if isinstance(request.get("report"), dict) else {}
    evidence = (
        report.get("claim_evidence_shadow", {})
        if isinstance(report.get("claim_evidence_shadow"), dict)
        else {}
    )
    metrics = evidence.get("metrics", {}) if isinstance(evidence.get("metrics"), dict) else {}
    aggregation = (
        evidence.get("aggregation", {})
        if isinstance(evidence.get("aggregation"), dict)
        else {}
    )
    status = str(evidence.get("status", "failed"))
    compact_metrics = {
        key: metrics[key]
        for key in (
            "claim_count",
            "certificate_count",
            "verified_claim_count",
            "critical_certificate_coverage",
            "unresolved_claim_count",
            "challenge_count",
            "loop_transition_count",
            "repeated_task_count",
            "fuse_triggered",
        )
        if key in metrics and isinstance(metrics[key], (int, float, bool))
    }
    failure_reasons = (
        ["claim-evidence-shadow-failed"] if status != "completed" else []
    )
    return candidate_archive.append_event(
        entry.parent.parent,
        entry,
        task_type="claim-evidence.shadow",
        actor="agent",
        event_type="evidence-shadow",
        status=status,
        summary="断言级正确性证据影子运行",
        request={
            "routing_tier": request.get("routing_tier"),
            "model_id": request.get("model_id"),
            "policy": correctness_policy.CORRECTNESS_POLICY_VERSION,
        },
        result={
            "status": status,
            "aggregation_status": str(aggregation.get("status", "not-available")),
            "metrics": compact_metrics,
            "canonical_answer_changed": False,
        },
        changed_files=["w3-shadow-report.json"],
        failure_reasons=failure_reasons,
    )


def queue_agent_job(kind: str, entry: Path, callback, *, routing_tier: str = "auto", model_id: str = "auto") -> dict:
    def guarded_callback():
        with visualization_lock(entry.name):
            if route_snapshot_is_stale(frozen_snapshot, LIBRARY):
                return {
                    "status": "failed",
                    "failure_type": "route_snapshot_stale",
                    "message": (
                        "作业入队后的模型注册表或生产路由配置已变化；"
                        "为避免静默更换模型，已失败关闭。请重新提交作业。"
                    ),
                    "route_snapshot": route_snapshot_summary(frozen_snapshot),
                }
            return callback()

    routing_tier = normalize_routing_tier(routing_tier)
    model_id = normalize_model_id(model_id)
    config = model_config_for_task(kind, model_id, routing_tier)
    frozen_snapshot = build_route_snapshot(
        kind=kind,
        routing_tier=routing_tier,
        requested_model_id=model_id,
        config=config,
        library=LIBRARY,
    )
    metadata: dict = {
        "routing_tier": routing_tier,
        "model_id": frozen_snapshot["resolved_model_id"] or model_id,
        "route_snapshot": route_snapshot_summary(frozen_snapshot),
    }
    if isinstance(config, dict):
        provider = str(config.get("provider", "")).strip()
        if provider:
            metadata["provider"] = provider
    return job_manager().submit(kind, entry.name, guarded_callback, metadata=metadata)


def visualization_lock(entry_id: str) -> threading.RLock:
    with VISUALIZATION_LOCKS_GUARD:
        return VISUALIZATION_LOCKS.setdefault(entry_id, threading.RLock())


def _remote_agent_allowed(entry: Path) -> bool:
    config = kb.load_json(entry.parent.parent / "config.json", {})
    return config.get("privacy", {}).get("allow_remote_agent") is True


def remote_agent_allowed() -> bool:
    config = kb.load_json(LIBRARY / "config.json", {})
    return config.get("privacy", {}).get("allow_remote_agent") is True


def agent_scheduler_config() -> dict:
    """Read scheduler config, writing defaults when the file is missing."""
    from agent_jobs import default_scheduler_config, normalize_scheduler_config

    config_dir = LIBRARY / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "agent-scheduler.json"
    if not config_path.is_file():
        defaults = default_scheduler_config()
        kb.write_json(config_path, defaults)
        return dict(defaults)
    raw = kb.load_json(config_path, {})
    return normalize_scheduler_config(raw)


# Model registry functions moved to model_registry.py


def _evidence_prompt_note(kind: str) -> str:
    """One-line reminder that current canonical content wins over historical evidence."""
    if kind == "analysis.generate":
        return (
            "当前题干优先于历史证据。历史片段只用于召回可迁移的高中方法、适用条件和易错点；"
            "必须独立验算，不得复制历史答案。"
        )
    if kind == "answer.revise":
        return "当前题干与教师意见优先于历史证据。历史片段只用于核对方法、易错点和适用条件。"
    if kind == "visualization.model":
        return "当前题干、答案和教师要求优先于历史证据。历史片段只用于核对方法和既往失败教训。"
    return ""


def agent_evidence_payload(
    entry: Path,
    kind: str,
    routing_tier: str = "auto",
    evidence_selection_policy: str = "baseline",
) -> dict:
    """Build a privacy-minimized evidence pack for one Agent task.

    Failure to retrieve is non-blocking; returns ``status=unavailable``.
    """
    task_type_map = {
        "analysis.generate": "analysis.generate",
        "answer.revise": "answer.revise",
        "visualization.model": "visualization.model",
    }
    task_type = task_type_map.get(kind, kind)

    parts: list[str] = []
    for name in ("problem.md", "student-solution.md", "teacher-solution.md", "solution.md"):
        path = entry / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    text = " ".join(parts)[:3000]

    if routing_tier == "economy":
        top_k, char_budget = 2, 3500
    elif routing_tier == "expert":
        top_k, char_budget = 4, 9000
    else:
        top_k, char_budget = 3, 8000

    try:
        from knowledge_store import build_agent_evidence
    except Exception:
        return {
            "schema_version": 1,
            "kind": "agent-evidence",
            "task_type": task_type,
            "status": "unavailable",
            "references": [],
        }
    try:
        library_root = entry.resolve().parent.parent  # entries/<id> → library root
        return build_agent_evidence(
            library_root,
            entry.name,
            text,
            task_type=task_type,
            top_k=top_k,
            char_budget=char_budget,
            selection_policy=evidence_selection_policy,
        )
    except Exception:
        return {
            "schema_version": 1,
            "kind": "agent-evidence",
            "task_type": task_type,
            "status": "unavailable",
            "references": [],
        }


def _agent_task(
    entry: Path,
    kind: str,
    prompt: str,
    allowed_paths: list[str],
    *,
    input_paths: list[str],
    denied_paths: list[str] | None = None,
    request_path: Path | None = None,
    requires_change: bool = True,
    routing_tier: str = "auto",
    model_config: dict | None = None,
    evidence: dict | None = None,
) -> dict:
    routing_tier = normalize_routing_tier(routing_tier)
    context_files: dict[str, str] = {}
    if kind in {"analysis.generate", "answer.revise"}:
        context_files[".agent-context/answer-template.md"] = str(
            PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "references" / "answer-template.md"
        )
    if kind == "analysis.generate" and routing_tier != "economy":
        context_files[".agent-context/secondary-conclusions.json"] = str(
            PROJECT_ROOT
            / ".claude"
            / "skills"
            / "build-physics-simulator"
            / "references"
            / "secondary-conclusions.json"
        )
    if routing_tier == "expert" and kind != "analysis.generate":
        context_files[".agent-context/library-skill.md"] = str(
            PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "SKILL.md"
        )
    if kind == "visualization.model":
        context_files.update({
            ".agent-context/simulator-skill.md": str(
                PROJECT_ROOT / ".claude" / "skills" / "build-physics-simulator" / "SKILL.md"
            ),
            ".agent-context/physics-model.schema.json": str(
                PROJECT_ROOT
                / ".claude"
                / "skills"
                / "build-physics-simulator"
                / "references"
                / "physics-model.schema.json"
            ),
        })
    scoped_prompt = "先读取 .agent-context/ 中的规则与模板。" + prompt
    if evidence:
        scoped_prompt += "\n" + _evidence_prompt_note(kind)

    task: dict = {
        "schema_version": 1,
        "id": uuid.uuid4().hex,
        "kind": kind,
        "entry_id": entry.name,
        "entry_dir": str(entry.resolve()),
        "working_dir": str(entry.resolve()),
        "request_path": str(request_path.resolve()) if request_path else "",
        "prompt": scoped_prompt,
        "allowed_paths": allowed_paths,
        "input_paths": input_paths,
        "denied_paths": denied_paths or [],
        "hidden_paths": sorted(source_asset_names(entry)),
        "requires_change": requires_change,
        "timeout_seconds": 1800,
        "allow_remote": _remote_agent_allowed(entry),
        "routing_tier": routing_tier,
        "model_config": model_config or {},
        "workspace_root": str(Path(tempfile.gettempdir()) / "wuli-agent-workspaces"),
        "context_files": context_files,
    }
    if evidence:
        task["context_payloads"] = {".agent-context/knowledge-evidence.json": evidence}
        evidence_context = {
            "status": str(evidence.get("status", "unavailable"))[:40],
            "reference_count": min(len(evidence.get("references", [])), 20),
            "task_type": str(evidence.get("task_type", kind))[:80],
        }
        context_budget = evidence.get("context_budget")
        if isinstance(context_budget, dict):
            evidence_context["budget"] = {
                key: context_budget[key]
                for key in ("requested_chars", "serialized_chars", "truncated")
                if key in context_budget
            }
        task["evidence_context"] = evidence_context
    return task


def answer_asset_names(entry: Path) -> set[str]:
    names: set[str] = set()
    for markdown_name in ("solution.md", "student-solution.md", "teacher-solution.md"):
        markdown_path = entry / markdown_name
        if not markdown_path.exists():
            continue
        for raw in kb.markdown_image_refs(markdown_path.read_text(encoding="utf-8")):
            relative = raw.strip().strip("<>").split(maxsplit=1)[0]
            if not relative.startswith(("http:", "https:", "data:")):
                names.add(Path(relative).as_posix())
    return names


def analysis_task(entry: Path, instruction: str, routing_tier: str = "auto", model_config: dict | None = None) -> dict:
    has_physics_model = (entry / "physics-model.json").is_file()
    prompt = (
        f"处理错题知识库条目。{instruction}\n"
        "已复核题干见 problem.md。检索已有方法，独立解题。\n"
        "返回一份结构化解析：完整学生版、仅教师增补的审计内容和教学元数据。"
        "不要直接编辑文件，不要重复输出教师版或兼容版，也不要设计图片；物理示意图由后续独立原子任务生成。\n"
        "若提供 visual-facts.json，它是 MiMo 从原题图提取并经来源复核的证据；解题必须使用且不得改写或抵触这些事实。\n"
        "生成前必须做方法自检：比较至少两条可行路径，选高中课程范围内认知负担最低、推导最短的一条；"
        "积分、导数等高阶方法即使可行也必须舍弃，改用图像面积、平均值、守恒或标准高中结论。\n"
        + (
            "本条目已有 physics-model.json；它记录已建模的事件、轨迹和答案语义。"
            "必须逐分支核对并保持一致，不得遗漏返回某区域后的后续运动。\n"
            if has_physics_model else ""
        )
        +
        "遵循授权上下文中的答案模板与格式要求。"
    )
    evidence = agent_evidence_payload(entry, "analysis.generate", routing_tier)
    task = _agent_task(
        entry,
        "analysis.generate",
        prompt,
        [
            "record.json",
            "student-solution.md",
            "teacher-solution.md",
            "solution.md",
        ],
        input_paths=[
            "problem.md",
            "record.json",
            "student-solution.md",
            "teacher-solution.md",
            "solution.md",
            *(["visual-facts.json"] if (entry / "visual-facts.json").is_file() else []),
            *(["physics-model.json"] if has_physics_model else []),
            *sorted(answer_asset_names(entry)),
        ],
        denied_paths=sorted(source_asset_names(entry)),
        routing_tier=routing_tier,
        model_config=model_config,
        evidence=evidence,
    )
    task["output_contract"] = analysis_artifacts.output_contract()
    task["structured_context_paths"] = [
        "problem.md",
        "record.json",
        *(["visual-facts.json"] if (entry / "visual-facts.json").is_file() else []),
        *(["physics-model.json"] if has_physics_model else []),
        ".agent-context/answer-template.md",
        ".agent-context/secondary-conclusions.json",
        ".agent-context/knowledge-evidence.json",
    ]
    return task


def core_analysis_task(
    entry: Path,
    instruction: str,
    target_brief: dict,
    routing_tier: str = "auto",
    model_config: dict | None = None,
) -> dict:
    """Build the one-call core solver task; rendering remains deterministic."""
    has_visual_facts = (entry / "visual-facts.json").is_file()
    has_physics_model = (entry / "physics-model.json").is_file()
    evidence = (
        agent_evidence_payload(entry, "analysis.generate", routing_tier)
        if target_brief.get("enhancements", {}).get("targeted_retrieval")
        else None
    )
    prompt = (
        f"处理已复核的物理题。{instruction}\n"
        "只完成一次核心求解：覆盖 Target Brief 中的每个目标，写最终结论、决定性推导、"
        "适用条件和复算检查。不要生成学生版/教师版 Markdown，不要分解成阶段接口，不要"
        "模拟第二求解器或仲裁器。visual-facts.json 与 physics-model.json 如存在均为已复核约束。"
    )
    task = _agent_task(
        entry,
        "analysis.generate",
        prompt,
        [
            "record.json",
            "core-solution.json",
            "student-solution.md",
            "teacher-solution.md",
            "solution.md",
        ],
        input_paths=[
            "problem.md",
            "record.json",
            *( ["visual-facts.json"] if has_visual_facts else []),
            *( ["physics-model.json"] if has_physics_model else []),
        ],
        denied_paths=sorted(source_asset_names(entry)),
        routing_tier=routing_tier,
        model_config=model_config,
        evidence=evidence,
    )
    # The core solver does not need the teaching template or simulator conclusion
    # catalogue; removing them keeps the single request compact and focused.
    task["context_files"] = {}
    task.setdefault("context_payloads", {})[
        ".agent-context/target-brief.json"
    ] = target_brief
    task["output_contract"] = core_analysis.output_contract(target_brief)
    task["structured_context_paths"] = [
        "problem.md",
        "record.json",
        *( ["visual-facts.json"] if has_visual_facts else []),
        *( ["physics-model.json"] if has_physics_model else []),
        ".agent-context/target-brief.json",
        *( [".agent-context/knowledge-evidence.json"] if evidence else []),
    ]
    return task


def physics_diagram_task(
    entry: Path,
    *,
    routing_tier: str = "auto",
    model_config: dict | None = None,
) -> dict:
    """Build the independent DeepSeek scene task after answer generation."""
    if not (entry / "visual-facts.json").is_file():
        raise ValueError("物理示意图需要先完成 MiMo visual-facts.json 来源复核")
    has_physics_model = (entry / "physics-model.json").is_file()
    visual_facts = kb.load_json(entry / "visual-facts.json", {})
    obligations = physics_diagram.build_obligations(
        visual_facts,
        (entry / "problem.md").read_text(encoding="utf-8"),
    )
    required_fact_lines = [
        f"- {item.get('id')}: {item.get('statement')}"
        for item in visual_facts.get("diagram_facts", [])
        if isinstance(item, dict)
        and item.get("kind") != "label"
        and float(item.get("confidence", 0) or 0) >= 0.75
    ]
    required_fact_note = (
        "以下高置信物理事实必须实际绑定到某个图元，不得放入 omissions：\n"
        + "\n".join(required_fact_lines)
        if required_fact_lines
        else "本题没有必须绑定的高置信非标签事实。高置信标签仍须绑定或在 omissions 说明。"
    )
    task = _agent_task(
        entry,
        "diagram.scene",
        (
            "根据已复核题干、MiMo 视觉事实和刚生成的答案，规划一张忠实的高中物理示意图。"
            "只返回强类型场景配方；从 component_catalog 选择并组合语义组件，不要输出 SVG，不要输出流程图，不要修改答案。"
            "必须逐项满足 diagram-obligations.json，并严格使用其中给定的视图槽位；"
            "空间投影必须合并到 motion，禁止自行增加第四面板。"
            + ("physics-model.json 是事件与轨迹真源；其轨迹、事件、边界顺序和坐标由编译器接管。" if has_physics_model else "")
            + "\n" + required_fact_note
        ),
        [
            physics_diagram.SCENE_PATH,
            physics_diagram.GATE_PATH,
            physics_diagram.SVG_PATH,
            physics_diagram.PROVENANCE_PATH,
            physics_diagram.REJECTED_SCENE_PATH,
            physics_diagram.DIAGNOSTICS_PATH,
        ],
        input_paths=[
            "problem.md",
            "visual-facts.json",
            "student-solution.md",
            "teacher-solution.md",
            "solution.md",
            "record.json",
            *( ["physics-model.json"] if has_physics_model else []),
        ],
        denied_paths=sorted(source_asset_names(entry)),
        requires_change=True,
        routing_tier=routing_tier,
        model_config=model_config,
    )
    task["output_contract"] = physics_diagram.output_contract()
    task["context_payloads"] = {physics_diagram.OBLIGATIONS_PATH: obligations}
    task["structured_context_paths"] = [
        "problem.md",
        "visual-facts.json",
        "student-solution.md",
        physics_diagram.OBLIGATIONS_PATH,
        *( ["physics-model.json"] if has_physics_model else []),
    ]
    return task


def physics_diagram_patch_task(
    entry: Path,
    candidate: dict,
    diagnostics: dict,
    *,
    routing_tier: str = "auto",
    model_config: dict | None = None,
) -> dict:
    """Build one bounded repair task over a rejected scene candidate."""
    task = _agent_task(
        entry,
        "diagram.scene",
        (
            "上轮物理图候选未通过预检。读取 rejected-physics-diagram-scene.json 和 "
            "physics-diagram-diagnostics.json，只修复诊断指出的最小依赖锥。"
            "返回 JSON Patch，不得重新生成整张场景，不得改变未被诊断影响的物理事实绑定。"
        ),
        [
            physics_diagram.SCENE_PATH,
            physics_diagram.GATE_PATH,
            physics_diagram.SVG_PATH,
            physics_diagram.PROVENANCE_PATH,
            physics_diagram.REJECTED_SCENE_PATH,
            physics_diagram.DIAGNOSTICS_PATH,
        ],
        input_paths=[
            "problem.md",
            "visual-facts.json",
            "student-solution.md",
            "teacher-solution.md",
            "solution.md",
            "record.json",
            *( ["physics-model.json"] if (entry / "physics-model.json").is_file() else []),
        ],
        denied_paths=sorted(source_asset_names(entry)),
        requires_change=True,
        routing_tier=routing_tier,
        model_config=model_config,
    )
    task["output_contract"] = physics_diagram.patch_output_contract()
    task["context_payloads"] = {
        physics_diagram.REJECTED_SCENE_PATH: candidate,
        physics_diagram.DIAGNOSTICS_PATH: diagnostics,
        physics_diagram.OBLIGATIONS_PATH: physics_diagram.build_obligations(
            kb.load_json(entry / "visual-facts.json", {}),
            (entry / "problem.md").read_text(encoding="utf-8"),
        ),
    }
    task["structured_context_paths"] = [
        physics_diagram.REJECTED_SCENE_PATH,
        physics_diagram.DIAGNOSTICS_PATH,
        physics_diagram.OBLIGATIONS_PATH,
    ]
    return task


def run_physics_diagram_gateway(
    entry: Path,
    *,
    routing_tier: str,
    model_config: dict | None,
    canonical_entry: Path | None = None,
) -> dict:
    """Run one scene generation and at most one minimal JSON-Patch repair."""
    task = physics_diagram_task(
        entry,
        routing_tier=routing_tier,
        model_config=model_config,
    )

    def validator(staging, changed):
        return validate_physics_diagram_candidate(staging, changed, canonical_entry or entry)

    initial = run_agent_gateway(
        entry,
        task,
        validator,
        materializer=lambda staging, payload: physics_diagram.materialize(
            staging, payload, model_config=model_config or {}
        ),
        bounded_failure_repair=False,
    )
    if initial.get("status") == "completed":
        initial["diagram_repair"] = {"status": "not-needed", "retry_count": 0}
        return initial
    context = physics_diagram.repair_context(initial)
    if context is None:
        initial["diagram_repair"] = {
            "status": "not-repairable",
            "retry_count": 0,
            "policy": "hard-gate-or-protocol-stop",
        }
        return initial
    candidate, diagnostics = context
    repair_task = physics_diagram_patch_task(
        entry,
        candidate,
        diagnostics,
        routing_tier=routing_tier,
        model_config=model_config,
    )
    repaired = run_agent_gateway(
        entry,
        repair_task,
        validator,
        materializer=lambda staging, payload: physics_diagram.materialize_patch(
            staging,
            payload,
            base_payload=candidate,
            model_config=model_config or {},
        ),
        bounded_failure_repair=False,
    )
    repaired["attempts"] = [
        *(initial.get("attempts") if isinstance(initial.get("attempts"), list) else []),
        *(repaired.get("attempts") if isinstance(repaired.get("attempts"), list) else []),
    ]
    repaired["diagram_repair"] = {
        "status": "recovered" if repaired.get("status") == "completed" else "exhausted",
        "retry_count": 1,
        "policy": "single-bounded-json-patch",
        "diagnostic_codes": [
            item.get("code") for item in diagnostics.get("diagnostics", []) if isinstance(item, dict)
        ],
    }
    return repaired


def w3_stage_task(
    entry: Path,
    stage: str,
    prompt: str,
    output_contract: dict,
    context_payloads: dict[str, dict],
    *,
    routing_tier: str,
    model_config: dict | None,
) -> dict:
    """Build one read-only structured W3 stage inside an analysis job."""
    has_physics_model = (entry / "physics-model.json").is_file()
    if (entry / "visual-facts.json").is_file():
        prompt += (
            "\nvisual-facts.json 是 MiMo 从原题图提取并经来源复核的证据。"
            "本阶段必须使用且不得改写、忽略或抵触其中事实与不确定性。"
        )
    task = _agent_task(
        entry,
        "analysis.generate",
        prompt,
        [],
        input_paths=[
            "problem.md",
            *(["visual-facts.json"] if (entry / "visual-facts.json").is_file() else []),
            *(["physics-model.json"] if has_physics_model else []),
        ],
        denied_paths=sorted(source_asset_names(entry)),
        requires_change=False,
        routing_tier=routing_tier,
        model_config=model_config,
    )
    task["id"] = f"{task['id']}-{stage}"
    task["context_files"] = {}
    if stage == "decompose":
        task["context_files"][".agent-context/decompose-skill.md"] = str(
            PROJECT_ROOT / ".claude" / "skills" / "decompose-physics-problem" / "SKILL.md"
        )
    task["context_payloads"] = context_payloads
    task["output_contract"] = output_contract
    task["structured_context_paths"] = [
        "problem.md",
        *(["visual-facts.json"] if (entry / "visual-facts.json").is_file() else []),
        *(["physics-model.json"] if has_physics_model else []),
        *sorted(context_payloads),
        *sorted(task["context_files"]),
    ]
    task["w3_stage"] = stage
    return task


def w3_stage_checkpoint_digest(
    *,
    stage: str,
    problem: str,
    context: dict,
    contract_name: str,
    model_id: str,
    routing_tier: str,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "stage": stage,
                "problem": problem,
                "context": context,
                "contract": contract_name,
                "model_id": model_id,
                "routing_tier": routing_tier,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def replay_w3_stage_checkpoint(
    checkpoint_path: Path,
    normalizer,
    *,
    include_runtime_identity: bool,
) -> dict | None:
    """Return a trusted normalized replay before any Gateway/provider call."""
    checkpoint = kb.load_json(checkpoint_path, {})
    if checkpoint.get("status") != "completed" or not isinstance(
        checkpoint.get("payload"), dict
    ):
        return None
    normalized = normalizer(checkpoint["payload"])
    if include_runtime_identity:
        normalized = {
            **normalized,
            "_runtime_identity": checkpoint.get("runtime_identity", {}),
        }
    return normalized


def summarize_w3_stage_timing(
    gateway: dict,
    *,
    elapsed_seconds: float,
) -> dict:
    provider_seconds = 0.0
    attempt_count = 0
    attempts = gateway.get("attempts", [])
    if isinstance(attempts, list):
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            attempt_count += 1
            try:
                provider_seconds += max(
                    float(attempt.get("duration_seconds", 0.0)),
                    0.0,
                )
            except (TypeError, ValueError):
                continue
    duration_seconds = round(max(float(elapsed_seconds), 0.0), 4)
    provider_seconds = round(provider_seconds, 4)
    return {
        "duration_seconds": duration_seconds,
        "provider_seconds": provider_seconds,
        "overhead_seconds": round(
            max(duration_seconds - provider_seconds, 0.0),
            4,
        ),
        "attempt_count": attempt_count,
    }


def visualization_task(
    entry: Path, message: str, request_path: Path, routing_tier: str = "auto", model_config: dict | None = None
) -> dict:
    has_model = (entry / "physics-model.json").exists()
    supported_types = ", ".join(SUPPORTED_SIMULATOR_MODEL_TYPES)
    task = (
        "当前尚无 physics-model.json。教师已明确请求生成可交互可视化；"
        "请从已复核题干和答案独立建立完整物理模型，写入 physics-model.json，并执行模型校验。"
        if not has_model
        else "当前已有 physics-model.json。请修正物理阶段、事件、轨迹、控件或文字，并执行模型校验。"
    )
    prompt = (
        f"教师要求：{message}\n\n"
        f"{task}\n"
        f"遵循 .agent-context/ 中的 build-physics-simulator Skill。"
        f"支持的 model_type：{supported_types}。必须选择其中一种，严禁自创 model_type；"
        "若都不适用，明确说明原因，不要写入无法构建的模型。"
        "静态解释 SVG 属于解析复核，不在此任务范围。执行模型校验即可。"
    )
    answer_assets = answer_asset_names(entry)
    evidence = agent_evidence_payload(entry, "visualization.model", routing_tier)
    return _agent_task(
        entry,
        "visualization.model",
        prompt,
        ["physics-model.json", "assets/visualization-*", "assets/simulation-*"],
        input_paths=[
            "problem.md",
            "record.json",
            "student-solution.md",
            "teacher-solution.md",
            "solution.md",
            "physics-model.json",
            request_path.name,
            "assets/visualization-*",
            "assets/simulation-*",
            "visualization/simulation-build.json",
        ],
        denied_paths=sorted(source_asset_names(entry) | answer_assets),
        request_path=request_path,
        requires_change=not has_model,
        routing_tier=routing_tier,
        model_config=model_config,
        evidence=evidence,
    )


def answer_revision_task(
    entry: Path, note: str, request_path: Path, routing_tier: str = "auto", model_config: dict | None = None
) -> dict:
    routing_tier = normalize_routing_tier(routing_tier)
    has_model = routing_tier != "economy" and (entry / "physics-model.json").exists()
    model_note = "若教师意见涉及物理模型的共同语义，可同步修正 physics-model.json。" if has_model else ""
    prompt = (
        f"教师意见：{note}\n\n"
        "根据以上意见修订错题解析。核对已批准题干，修改 student-solution.md、teacher-solution.md，"
        "保持 solution.md 与教师版一致。可修改或新增 assets/ 中的解释 SVG/PNG。"
        f"{model_note}"
        "采用高中生应知的低认知负担解法；先比较可行路径并自检，学生版只保留最短主线，"
        "不得使用积分、导数、微分方程、矩阵、复数法或大学力学方法，主线不超过五步。"
        "遵循 .agent-context/ 中的答案模板与格式要求。"
    )
    allowed = ["solution.md", "student-solution.md", "teacher-solution.md", "assets/**"]
    if has_model:
        allowed.append("physics-model.json")
    inputs = [
        "problem.md",
        "student-solution.md",
        "teacher-solution.md",
        "solution.md",
        request_path.name,
        *sorted(answer_asset_names(entry)),
    ]
    if has_model:
        inputs.append("physics-model.json")
    evidence = agent_evidence_payload(entry, "answer.revise", routing_tier)
    return _agent_task(
        entry,
        "answer.revise",
        prompt,
        allowed,
        input_paths=inputs,
        denied_paths=sorted(source_asset_names(entry)),
        request_path=request_path,
        routing_tier=routing_tier,
        model_config=model_config,
        evidence=evidence,
    )


def entry_file_digests(entry: Path) -> dict[str, str]:
    """Snapshot current-entry files for a post-Agent scope audit."""
    return {str(path.relative_to(entry)): kb.sha256_file(path) for path in sorted(entry.rglob("*")) if path.is_file()}


def changed_entry_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))


def source_asset_names(entry: Path) -> set[str]:
    record = kb.load_json(entry / "record.json", {})
    names = {str(Path(name)) for name in record.get("source", {}).get("stored_files", [])}
    assets = entry / "assets"
    if assets.is_dir():
        names.update(
            path.relative_to(entry).as_posix()
            for path in assets.glob("original.*")
            if path.is_file() and not path.is_symlink()
        )
    return names


def validate_source_clean_candidate(
    staging: Path, _changed: list[str], canonical_entry: Path | None = None
) -> list[str]:
    """Domain validation for source.clean: problem.md exists and title is content-based."""
    errors: list[str] = []
    problem = staging / "problem.md"
    if not problem.is_file():
        errors.append("problem.md is missing")
    elif len(problem.read_text(encoding="utf-8").strip()) < 30:
        errors.append("题干内容过短")
    if canonical_entry is not None:
        baseline = kb.load_json(canonical_entry / "record.json", {})
        if "record.json" in _changed:
            candidate = kb.load_json(staging / "record.json", {})
            for field in sorted(PROTECTED_RECORD_FIELDS):
                if candidate.get(field) != baseline.get(field):
                    errors.append(f"record.json protected field changed: {field}")
        else:
            _safe_validation_copy(canonical_entry, staging, "record.json")
        for relative in baseline.get("source", {}).get("stored_files", []):
            _safe_validation_copy(canonical_entry, staging, str(relative))
    return sorted(set(errors))


def source_clean_task(entry: Path, routing_tier: str = "auto", model_config: dict | None = None) -> dict:
    """Construct an Agent task that cleans OCR text and sets a content-based title."""
    routing_tier = source_clean_routing_tier(routing_tier)
    prompt = (
        "整理错题条目的 OCR 题干草稿。\n"
        "1. 读取 problem.md，修正 OCR 识别错误（公式、符号、下标、换行），保留原题完整信息。\n"
        "2. 从题干内容中提取一个有意义的中文标题（例如'带电粒子在磁场中的圆周运动'），"
        "写入 record.json 的 title 字段。不要保留'XX练习题 第N页'之类的文件名作为标题。\n"
        "3. 可初步标注 record.json 的 knowledge_points、difficulty、grade。\n"
        "record.json 的其他字段为保护字段，不可修改。\n"
        "只消费 problem.md 文本与已存在且与当前原图匹配的 visual-facts.json；"
        "不得请求或上传任何原图。视觉事实缺失或过期时不要自行补视觉调用。"
    )
    return _agent_task(
        entry,
        "source.clean",
        prompt,
        ["problem.md", "record.json"],
        input_paths=["problem.md", "record.json"],
        denied_paths=sorted(source_asset_names(entry)),
        requires_change=True,
        routing_tier=routing_tier,
        model_config=model_config,
    )


def _safe_validation_copy(canonical_entry: Path, staging: Path, relative: str) -> None:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        return
    source = canonical_entry / rel
    target = staging / rel
    if source.is_file() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def validate_answer_candidate(
    staging: Path,
    _changed: list[str],
    canonical_entry: Path | None = None,
    *,
    allow_pending_diagram: bool = False,
    method_profile: str = "high_school_standard",
    allow_missing_explanatory_image: bool = False,
) -> list[str]:
    errors: list[str] = []
    student = staging / "student-solution.md"
    teacher = staging / "teacher-solution.md"
    solution = staging / "solution.md"
    if not student.is_file():
        errors.append("student-solution.md is missing")
    if not teacher.is_file():
        errors.append("teacher-solution.md is missing")
    if not solution.is_file():
        errors.append("solution.md is missing")
    if teacher.is_file() and solution.is_file() and teacher.read_bytes() != solution.read_bytes():
        errors.append("solution.md must be identical to teacher-solution.md")
    if student.is_file() and "student-solution.md" in _changed:
        errors.extend(
            analysis_artifacts.student_method_errors(
                student.read_text(encoding="utf-8"), method_profile
            )
        )
    svg_path = staging / analysis_artifacts.EXPLANATION_PATH
    svg_changed = analysis_artifacts.EXPLANATION_PATH in _changed
    provenance_changed = "svg-provenance.json" in _changed
    if svg_path.is_file() and svg_changed:
        try:
            svg_collaboration.validate_svg_safety(svg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"unsafe explanatory SVG: {exc}")
    provenance_path = staging / "svg-provenance.json"
    facts_path = staging / "visual-facts.json"
    if (
        provenance_path.is_file()
        and facts_path.is_file()
        and svg_path.is_file()
        and (svg_changed or provenance_changed)
    ):
        provenance = kb.load_json(provenance_path, {})
        identity = provenance.get("model_identity", {}) if isinstance(provenance, dict) else {}
        trace = {
            **identity,
            "generation_fingerprint": provenance.get("generation_fingerprint", "")
            if isinstance(provenance, dict) else "",
        }
        try:
            expected = svg_collaboration.validate_and_bind_svg(
                svg_path.read_text(encoding="utf-8"),
                kb.load_json(facts_path, {}),
                trace,
                identity,
            )
            if expected != provenance:
                errors.append("svg-provenance.json does not match current facts, route, or SVG")
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"invalid SVG provenance: {exc}")
    if canonical_entry is not None:
        baseline = kb.load_json(canonical_entry / "record.json", {})
        if "record.json" in _changed:
            candidate = kb.load_json(staging / "record.json", {})
            for field in sorted(PROTECTED_RECORD_FIELDS):
                if candidate.get(field) != baseline.get(field):
                    errors.append(f"record.json protected field changed: {field}")
        else:
            _safe_validation_copy(canonical_entry, staging, "record.json")
        for relative in baseline.get("source", {}).get("stored_files", []):
            _safe_validation_copy(canonical_entry, staging, str(relative))
    kb_errors = kb.validate_entry(LIBRARY, staging, ready_rules=True, require_answer_review=False)
    if allow_pending_diagram:
        kb_errors = [
            error for error in kb_errors
            if error != f"solution.md: missing image {analysis_artifacts.EXPLANATION_PATH}"
        ]
    if allow_missing_explanatory_image:
        kb_errors = [
            error
            for error in kb_errors
            if error != "solution.md: at least one explanatory/source image is required"
            and not error.startswith("solution.md: missing image ")
        ]
    errors.extend(kb_errors)
    return sorted(set(errors))


def validate_physics_diagram_candidate(
    staging: Path,
    changed: list[str],
    canonical_entry: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    diagnostic_path = staging / physics_diagram.DIAGNOSTICS_PATH
    rejected_path = staging / physics_diagram.REJECTED_SCENE_PATH
    accepted_paths = (
        physics_diagram.SCENE_PATH,
        physics_diagram.GATE_PATH,
        physics_diagram.SVG_PATH,
        physics_diagram.PROVENANCE_PATH,
    )
    if (diagnostic_path.is_file() or rejected_path.is_file()) and not all(
        (staging / relative).is_file() for relative in accepted_paths
    ):
        report = kb.load_json(diagnostic_path, {})
        diagnostics = report.get("diagnostics", []) if isinstance(report, dict) else []
        for item in diagnostics if isinstance(diagnostics, list) else []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code", "scene.invalid"))
            path = str(item.get("path", ""))
            message = str(item.get("message", "physics diagram preflight failed"))
            errors.append(f"{code}{' at ' + path if path else ''}: {message}")
        if not errors:
            errors.append("physics diagram preflight failed without diagnostics")
        return sorted(set(errors))
    for relative in accepted_paths:
        if not (staging / relative).is_file():
            errors.append(f"{relative} is missing")
    if not errors:
        try:
            scene = kb.load_json(staging / physics_diagram.SCENE_PATH, {})
            scene = physics_diagram.validate_canonical_scene(scene)
            gate = kb.load_json(staging / physics_diagram.GATE_PATH, {})
            facts = kb.load_json(staging / "visual-facts.json", {})
            physics_model = (
                kb.load_json(staging / "physics-model.json", {})
                if (staging / "physics-model.json").is_file()
                else None
            )
            expected = physics_diagram.semantic_gate(
                scene,
                facts,
                (staging / "problem.md").read_text(encoding="utf-8"),
                physics_model,
                compilation=gate.get("compilation"),
            )
            if gate != expected or gate.get("status") != "passed":
                errors.append("physics-diagram-gate.json does not match the current scene and facts")
            expected_svg = physics_diagram.render_svg(scene)
            if (staging / physics_diagram.SVG_PATH).read_text(encoding="utf-8") != expected_svg:
                errors.append("explanatory SVG is not the deterministic rendering of the typed scene")
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"invalid physics diagram candidate: {exc}")
    errors.extend(validate_answer_candidate(staging, changed, canonical_entry))
    return sorted(set(errors))


def validate_visualization_candidate(staging: Path, _changed: list[str]) -> list[str]:
    if not (staging / "physics-model.json").is_file():
        return ["physics-model.json is missing"]
    with tempfile.TemporaryDirectory(prefix=".agent-model-check-") as output_name:
        report = process_uploads.build_simulator(staging, Path(output_name), "skip")
    if report.get("status") == "ok":
        return []
    if report.get("errors"):
        return report["errors"]
    if report.get("status") == "unsupported":
        supported = ", ".join(str(item) for item in report.get("supported", [])) or ", ".join(
            SUPPORTED_SIMULATOR_MODEL_TYPES
        )
        return [f"unsupported model_type: {report.get('model_type')}; supported: {supported}"]
    validation = report.get("validation")
    if isinstance(validation, dict):
        details = validation.get("stderr") or validation.get("stdout")
        if details:
            return [f"visualization model validation failed: {report.get('status')}: {str(details)[:800]}"]
    return [f"visualization model validation failed: {report.get('status', 'unknown')}"]


def gateway_failure_detail(gateway: dict, fallback: str) -> str:
    parts: list[str] = []
    message = str(gateway.get("message") or "").strip()
    if message:
        parts.append(message)
    failure_type = str(gateway.get("failure_type") or "").strip()
    if failure_type:
        parts.append(f"失败类型：{failure_type}")
    validation = gateway.get("validation_errors")
    if isinstance(validation, list) and validation:
        parts.append("校验原因：" + "；".join(str(item) for item in validation[:5]))
    unauthorized = gateway.get("unauthorized_changes")
    if isinstance(unauthorized, list) and unauthorized:
        parts.append("越权文件：" + "、".join(str(item) for item in unauthorized[:8]))
    changed = gateway.get("changed_files")
    if isinstance(changed, list) and changed:
        parts.append("候选改动：" + "、".join(str(item) for item in changed[:8]))
    provider = gateway.get("provider")
    if provider:
        parts.append(f"provider：{provider}")
    repair = gateway.get("failure_repair")
    if isinstance(repair, dict) and repair.get("action"):
        parts.append("建议：" + str(repair["action"]))
    return "\n".join(parts) if parts else fallback


def mark_answer_needs_review(library: Path, entry: Path, note: str) -> dict:
    changed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    review = {
        "schema_version": 1,
        "entry_id": entry.name,
        "status": "needs-review",
        "reviewer": "",
        "reviewed_at": "",
        "answer_digest": process_uploads.answer_digest(entry),
        "note": note,
        "changed_at": changed_at,
    }
    record = kb.load_json(entry / "record.json", {})
    record["status"] = "needs-review"
    record["answer_status"] = "pending"
    record["answer_review"] = review
    record["updated_at"] = changed_at
    kb.write_json(entry / "record.json", record)
    kb.write_json(entry / "answer-review.json", review)
    state = process_uploads.pipeline_state(entry)
    pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry.name})
    pipeline.update({"state": state["state"], "answer_review": review})
    kb.write_json(entry / "pipeline.json", pipeline)
    with LIBRARY_INDEX_LOCK:
        kb.rebuild_index(library)
    return {"review": review, "state": state}


def save_answer_entry(library: Path, entry: Path, data: dict) -> dict:
    layer = str(data.get("layer", ""))
    markdown = str(data.get("markdown", ""))
    base_digest = str(data.get("base_digest", ""))
    if layer not in {"student", "teacher"}:
        raise ValueError("answer layer must be student or teacher")
    if len(markdown.strip()) < 30:
        raise ValueError("解析内容过短")
    if base_digest and base_digest != process_uploads.answer_digest(entry):
        raise ValueError("答案已在其他位置发生变化，请刷新后再编辑")
    target = entry / ("student-solution.md" if layer == "student" else "teacher-solution.md")
    before_text = target.read_text(encoding="utf-8") if target.is_file() else ""
    before_digest = process_uploads.answer_digest(entry)
    kb.write_text(target, markdown)
    if layer == "teacher":
        kb.write_text(entry / "solution.md", markdown)
    model_path = entry / "physics-model.json"
    if model_path.exists():
        model = kb.load_json(model_path, {})
        source = model.setdefault("source", {})
        source["answer_render_mode"] = "manual"
        source["manual_answer_files"] = {
            "student": "student-solution.md",
            "teacher": "teacher-solution.md",
        }
        kb.write_json(model_path, model)
    marked = mark_answer_needs_review(library, entry, "答案已在教师工作台编辑，等待重新复核")
    assessment = assess_entry_difficulty(entry)
    evaluation = evaluator.evaluate_entry(library, entry.name, write=True)
    semantic_diff = teacher_feedback.semantic_text_diff(before_text, markdown, path=target.name)
    semantic_diff["change_ratio"] = round(
        semantic_diff["changed_lines"] / max(semantic_diff["total_lines"], 1), 4
    )
    semantic_diff["status"] = "computed"
    source_event = candidate_archive.latest_event(
        entry, task_types={"analysis.generate", "answer.revise"}, event_type="agent-result"
    )
    archive = candidate_archive.append_event(
        library,
        entry,
        task_type="answer.save",
        actor="teacher",
        event_type="edit",
        status="saved",
        summary=f"教师保存{layer}答案层",
        request={"layer": layer, "base_digest": before_digest},
        result={"answer_digest": marked["review"]["answer_digest"], "semantic_diff": semantic_diff},
        evaluation={
            key: evaluation.get(key)
            for key in ("status", "scores", "teacher_review_required", "failure_reasons", "warning_reasons")
        },
        changed_files=[target.name, *(["solution.md"] if layer == "teacher" else [])],
        feedback={"categories": semantic_diff["categories"]},
        links={"candidate_event_id": source_event.get("event_id")} if source_event else {},
    )
    return {
        "status": "saved",
        "layer": layer,
        "answer_digest": marked["review"]["answer_digest"],
        "state": marked["state"],
        "evaluation": evaluation,
        "difficulty_assessment": assessment,
        "semantic_diff": semantic_diff,
        "archive": archive,
    }


def assess_entry_difficulty(entry: Path, *, force: bool = False) -> dict:
    """Refresh objective difficulty from the reviewed problem and standard path."""
    record = kb.load_json(entry / "record.json", {})
    problem = (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").is_file() else ""
    standard_path = record.get("standard_solution_path")
    previous = record.get("difficulty_assessment")
    if not force and difficulty_assessment.current(previous, problem, standard_path):
        return previous
    if isinstance(previous, dict) and previous.get("status") == "teacher-edited":
        history = record.setdefault("difficulty_assessment_history", [])
        if isinstance(history, list):
            history.append({"reason": "题干或标准解题路径已变化，保留原教师校准", "assessment": previous})
            record["difficulty_assessment_history"] = history[-10:]
    assessment = difficulty_assessment.auto_assess(record, problem, standard_path)
    if isinstance(previous, dict) and previous.get("status") == "teacher-edited":
        assessment["calibration"] = {
            "status": "needs-recalibration",
            "note": "题干或标准解题路径已变化；已保留先前教师校准，新自动基线先默认生效。",
        }
    record["difficulty_assessment"] = assessment
    # Keep the legacy label coherent for existing stats and retrieval views.
    if assessment.get("score") is not None:
        record["difficulty"] = assessment["level"]
    record["updated_at"] = difficulty_assessment.now_iso()
    kb.write_json(entry / "record.json", record)
    with LIBRARY_INDEX_LOCK:
        kb.rebuild_index(entry.parent.parent)
    return assessment


def save_difficulty_assessment(entry: Path, data: dict) -> dict:
    record = kb.load_json(entry / "record.json", {})
    problem = (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").is_file() else ""
    standard_path = record.get("standard_solution_path")
    current = record.get("difficulty_assessment")
    baseline = current.get("auto_baseline") if isinstance(current, dict) else None
    assessment = difficulty_assessment.normalize_teacher_edit(
        data.get("assessment"),
        problem,
        standard_path,
        baseline=baseline,
    )
    record["difficulty_assessment"] = assessment
    record["difficulty"] = assessment["level"]
    record["updated_at"] = difficulty_assessment.now_iso()
    kb.write_json(entry / "record.json", record)
    with LIBRARY_INDEX_LOCK:
        kb.rebuild_index(entry.parent.parent)
    return {"status": "saved", "difficulty_assessment": assessment}


def retrieval_review_snapshot(library: Path) -> dict:
    """Build a read-only snapshot of retrieval-review cases with candidate cards.

    Each candidate card exposes a thumbnail, title, and a short problem excerpt
    so the teacher can visually judge relevance without seeing private solutions.
    """
    cases_path = retrieval_benchmark.default_cases_path(library)
    cases = retrieval_benchmark.load_cases(cases_path)
    validation = retrieval_benchmark.validate_cases(library, cases)
    entry_dirs = list(kb.entry_dirs(library))
    candidates: list[dict] = []
    for entry_dir in entry_dirs:
        record = kb.load_json(entry_dir / "record.json", {})
        stored = record.get("source", {}).get("stored_files", [])
        thumbnail = ""
        for relative in stored:
            if Path(relative).suffix.lower() in kb.IMAGE_EXTENSIONS and (entry_dir / relative).exists():
                thumbnail = f"/api/entry-file/{entry_dir.name}/{Path(relative).as_posix()}"
                break
        problem_text = ""
        problem_path = entry_dir / "problem.md"
        if problem_path.is_file():
            problem_text = problem_path.read_text(encoding="utf-8")
        excerpt = problem_text[:200].replace("\n", " ").strip()
        candidates.append({
            "id": entry_dir.name,
            "title": str(record.get("title") or entry_dir.name),
            "problem_excerpt": excerpt,
            "thumbnail": thumbnail,
            "knowledge_points": [str(item) for item in record.get("knowledge_points", [])[:8]],
            "error_types": [str(item) for item in record.get("error_types", [])[:8]],
        })
    candidates.sort(key=lambda item: str(item.get("id", "")))
    return {
        "cases": [{key: value for key, value in case.items() if key != "_line"} for case in cases],
        "candidates": candidates,
        "validation": validation,
    }


def save_retrieval_review(data: dict, library: Path) -> dict:
    """Save a teacher-reviewed retrieval case, then return an updated snapshot."""
    case_id = str(data.get("id", "")).strip()
    query = str(data.get("query", "")).strip()
    category = str(data.get("category", "")).strip()
    review_status = str(data.get("review_status", "")).strip()
    relevant_ids = data.get("relevant_entry_ids")
    if not isinstance(relevant_ids, list) or not relevant_ids:
        raise ValueError("至少勾选一道相关题目才能批准")
    if not case_id or not query:
        raise ValueError("id 和 query 为必填字段")
    if category not in retrieval_benchmark.CATEGORIES:
        raise ValueError(f"category 必须是以下之一: {', '.join(sorted(retrieval_benchmark.CATEGORIES))}")
    if review_status not in retrieval_benchmark.REVIEW_STATUSES:
        raise ValueError(f"review_status 必须是以下之一: {', '.join(sorted(retrieval_benchmark.REVIEW_STATUSES))}")

    cases_path = retrieval_benchmark.default_cases_path(library)
    cases = retrieval_benchmark.load_cases(cases_path)
    existing_ids = {str(c.get("id", "")).strip() for c in cases}
    if case_id not in existing_ids:
        raise ValueError(f"case id {case_id} 不在当前评测集中，无法审批")

    entry_ids = {entry.name for entry in kb.entry_dirs(library)}
    for eid in relevant_ids:
        if str(eid) not in entry_ids:
            raise ValueError(f"条目 {eid} 不在知识库中，不可用于评测")

    updated: list[dict] = []
    for case in cases:
        if str(case.get("id", "")).strip() == case_id:
            case = dict(case)
            case["query"] = query
            case["category"] = category
            case["review_status"] = review_status
            case["relevant_entry_ids"] = sorted(set(str(eid) for eid in relevant_ids))
        updated.append(case)
    retrieval_benchmark.write_cases(cases_path, updated)
    return retrieval_review_snapshot(library)


# ---------------------------------------------------------------------------
# Agent baseline snapshot and teacher-edit diff  (efficiency evaluator feed)
# ---------------------------------------------------------------------------

_LATEX_NORMALIZATIONS = [
    (re.compile(r"\\dfrac\b"), r"\\frac"),
    (re.compile(r"\\tfrac\b"), r"\\frac"),
    (re.compile(r"\\mathbf\b"), r"\\vec"),
    (re.compile(r"\\bm\b"), r"\\vec"),
    (re.compile(r"\\bigl\b"), r"\\left"),
    (re.compile(r"\\bigr\b"), r"\\right"),
    (re.compile(r"\\Bigl\b"), r"\\left"),
    (re.compile(r"\\Bigr\b"), r"\\right"),
    (re.compile(r"\\biggl\b"), r"\\left"),
    (re.compile(r"\\biggr\b"), r"\\right"),
    (re.compile(r"\\Biggl\b"), r"\\left"),
    (re.compile(r"\\Biggr\b"), r"\\right"),
    (re.compile(r"\\displaystyle\b"), r""),
    (re.compile(r"\\textstyle\b"), r""),
    (re.compile(r"\\scriptstyle\b"), r""),
    (re.compile(r"\\limits"), r""),
    (re.compile(r"\\nolimits"), r""),
    (re.compile(r"\\qquad\b"), r" "),
    (re.compile(r"\\quad\b"), r" "),
    (re.compile(r"\\enspace\b"), r" "),
    (re.compile(r"\\;"), r" "),
    (re.compile(r"\\:"), r" "),
    (re.compile(r"\\,"), r" "),
    (re.compile(r"\\!"), r""),
]


def _normalize_latex(text: str) -> str:
    """Normalise LaTeX formatting differences so only semantic changes count."""
    for pattern, replacement in _LATEX_NORMALIZATIONS:
        text = pattern.sub(replacement, text)
    return text


def _save_agent_baseline(entry: Path, changed_files: list[str], *, task_type: str = "") -> Path | None:
    """Snapshot agent-promoted files as the baseline for later teacher-edit diff."""
    if not changed_files:
        return None
    baseline_dir = entry / ".agent-baseline"
    if baseline_dir.exists():
        shutil.rmtree(baseline_dir)
    baseline_dir.mkdir(parents=True)
    for relative in changed_files:
        source = entry / relative
        if source.is_file() and not source.is_symlink():
            target = baseline_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    relevant_at_baseline = {
        path
        for path in ("solution.md", "student-solution.md", "teacher-solution.md", "physics-model.json")
        if (entry / path).is_file()
    }
    if (entry / "assets").is_dir():
        relevant_at_baseline.update(
            path.relative_to(entry).as_posix() for path in (entry / "assets").glob("explanat*.*") if path.is_file()
        )
    kb.write_json(
        baseline_dir / "baseline.json",
        {
            "schema_version": 1,
            "task_type": task_type,
            "changed_files": sorted(set(changed_files)),
            "relevant_files_at_baseline": sorted(relevant_at_baseline),
            "created_at": kb.now_iso(),
        },
    )
    return baseline_dir


def _compute_agent_diff(entry: Path) -> dict:
    """Compare the Agent baseline with the teacher-reviewed files.

    Text artifacts use a normalized line diff. Binary explanation assets count
    as one unit so a PNG/JPEG never blocks the answer-approval endpoint.
    """
    baseline_dir = entry / ".agent-baseline"
    if not baseline_dir.exists():
        return {"status": "no-baseline", "changed_lines": 0, "total_lines": 0, "change_ratio": 0.0}
    metadata = kb.load_json(baseline_dir / "baseline.json", {})
    tracked = {
        path.relative_to(baseline_dir).as_posix()
        for path in baseline_dir.rglob("*")
        if path.is_file() and path.name != "baseline.json"
    }
    tracked.update(str(item) for item in metadata.get("changed_files", []) if isinstance(item, str))
    for path in ("solution.md", "student-solution.md", "teacher-solution.md", "physics-model.json"):
        if (entry / path).is_file() and (baseline_dir / path).is_file():
            tracked.add(path)
    baseline_relevant = set(metadata.get("relevant_files_at_baseline", []))
    current_relevant = {
        path
        for path in ("solution.md", "student-solution.md", "teacher-solution.md", "physics-model.json")
        if (entry / path).is_file()
    }
    if (entry / "assets").is_dir():
        current_relevant.update(
            path.relative_to(entry).as_posix() for path in (entry / "assets").glob("explanat*.*") if path.is_file()
        )
    tracked.update(current_relevant - baseline_relevant)

    total_changed = 0
    total_lines = 0
    categories: set[str] = set()
    critical_correction = False
    file_changes = []
    for relative in sorted(tracked):
        baseline_file = baseline_dir / relative
        current_file = entry / relative
        baseline_bytes = baseline_file.read_bytes() if baseline_file.is_file() else b""
        try:
            baseline_text = baseline_bytes.decode("utf-8")
        except UnicodeDecodeError:
            baseline_text = None
        if not baseline_file.is_file():
            current_bytes = current_file.read_bytes()
            try:
                current_text = current_bytes.decode("utf-8")
            except UnicodeDecodeError:
                current_text = None
            units = max(len(current_text.splitlines()), 1) if current_text is not None else 1
            total_changed += units
            total_lines += units
            semantic = teacher_feedback.semantic_text_diff("", current_text or "", path=relative)
            categories.update(semantic["categories"])
            critical_correction = critical_correction or semantic["critical_correction"]
            file_changes.append({"path": relative, "change": "added", "changed_units": units})
            continue
        if not current_file.is_file():
            units = max(len(baseline_text.splitlines()), 1) if baseline_text is not None else 1
            total_changed += units
            total_lines += units
            categories.update(teacher_feedback.feedback_categories("", changed_files=[relative]))
            file_changes.append({"path": relative, "change": "deleted", "changed_units": units})
            continue
        current_bytes = current_file.read_bytes()
        try:
            current_text = current_bytes.decode("utf-8")
        except UnicodeDecodeError:
            current_text = None
        if baseline_text is None or current_text is None:
            changed_units = 0 if baseline_bytes == current_bytes else 1
            total_changed += changed_units
            total_lines += 1
            if changed_units:
                categories.update(teacher_feedback.feedback_categories("", changed_files=[relative]))
                file_changes.append({"path": relative, "change": "modified", "changed_units": changed_units})
            continue
        old_norm = _normalize_latex(baseline_text)
        new_norm = _normalize_latex(current_text)
        old_lines = old_norm.splitlines()
        new_lines = new_norm.splitlines()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        changed = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                changed += max(i2 - i1, j2 - j1)
        total_changed += changed
        total_lines += max(len(old_lines), 1)
        if changed:
            semantic = teacher_feedback.semantic_text_diff(old_norm, new_norm, path=relative)
            categories.update(semantic["categories"])
            critical_correction = critical_correction or semantic["critical_correction"]
            file_changes.append({"path": relative, "change": "modified", "changed_units": changed})
    change_ratio = round(total_changed / max(total_lines, 1), 4)
    return {
        "status": "computed",
        "changed_lines": total_changed,
        "total_lines": total_lines,
        "change_ratio": change_ratio,
        "changed_files": [item["path"] for item in file_changes],
        "file_changes": file_changes,
        "categories": sorted(categories),
        "critical_correction": critical_correction,
        "source_task": metadata.get("task_type", ""),
    }


def read_json(path: Path, default=None):
    return kb.load_json(path, default)


def safe_entry(entry_id: str) -> Path:
    if not entry_id or Path(entry_id).name != entry_id:
        raise ValueError("invalid entry id")
    entry = (LIBRARY / "entries" / entry_id).resolve()
    entry.relative_to((LIBRARY / "entries").resolve())
    if not entry.exists():
        raise FileNotFoundError(entry_id)
    return entry


def safe_child(root: Path, relative: str) -> Path:
    target = (root / unquote(relative)).resolve()
    target.relative_to(root.resolve())
    if not target.is_file():
        raise FileNotFoundError(relative)
    return target


def entry_summary(entry: Path) -> dict:
    record = read_json(entry / "record.json", {})
    state = process_uploads.pipeline_state(entry)
    images = []
    for relative in record.get("source", {}).get("stored_files", []):
        if Path(relative).suffix.lower() in kb.IMAGE_EXTENSIONS and (entry / relative).exists():
            images.append(f"/api/entry-file/{quote(entry.name)}/{quote(relative)}")
    return {
        "id": entry.name,
        "title": record.get("title") or entry.name,
        "subject": record.get("subject", ""),
        "updated_at": record.get("updated_at", ""),
        "library_folder": record.get("library_folder", kb.default_library_folder(record, entry.name)),
        "state": state["state"],
        "next_action": state["next_action"],
        "source_review": state["source_review"],
        "answer_review": state["answer_review"],
        "difficulty_assessment": record.get("difficulty_assessment"),
        "thumbnail": images[0] if images else None,
    }


def delivery_files(entry: Path) -> list[dict]:
    delivery = read_json(entry / "delivery.json", {})
    output = Path(delivery.get("output", "")) if delivery.get("output") else None
    if not output or not output.exists():
        return []
    files = []
    for relative in delivery.get("files", []):
        metadata = DELIVERY_CATALOG.get(relative)
        if not metadata:
            continue
        path = output / relative
        if path.is_file() and not any(part.startswith(".") for part in Path(relative).parts):
            files.append({
                "name": Path(relative).name,
                "relative": relative,
                "size": path.stat().st_size,
                "url": f"/api/download/{quote(entry.name)}/{quote(relative)}",
                **metadata,
            })
    return sorted(files, key=lambda item: item["order"])


def entry_detail(entry: Path) -> dict:
    summary = entry_summary(entry)
    record = read_json(entry / "record.json", {})
    visualization = process_uploads.visualization_snapshot(entry)
    preview_url = None
    if visualization.get("html"):
        preview_url = (
            f"/api/visualization/{quote(entry.name)}/physics-simulator.html?v={visualization['artifact_digest'][:12]}"
        )
    visualization.pop("html", None)
    publication = public_site.publication_snapshot(entry, PUBLIC_SITE)
    publication_images = public_site.public_image_snapshot(entry)
    latest_job = job_manager().latest_for_entry(entry.name)
    w3_shadow_raw = read_json(entry / "w3-shadow-report.json", {})
    w3_report = w3_shadow_raw.get("report", {}) if w3_shadow_raw.get("status") == "completed" else {}
    w3_shadow = {
        "status": str(w3_shadow_raw.get("status", "not-run")),
        "mode": str(w3_report.get("mode", "shadow")) if w3_report else "shadow",
        "teacher_review_focus": w3_pipeline.teacher_review_snapshot(w3_report),
        "claim_evidence": w3_pipeline.claim_evidence_teacher_snapshot(w3_report),
        "metrics": {
            key: w3_report.get("metrics", {}).get(key)
            for key in ("target_count", "verified_target_count", "solver_b_used")
            if key in w3_report.get("metrics", {})
        },
    }
    for source in publication_images.get("sources", []):
        source["url"] = f"/api/entry-file/{quote(entry.name)}/{quote(source['relative'])}"
    if publication["preview_ready"]:
        publication["preview_url"] = (
            f"/api/public-preview/{quote(entry.name)}/viewer.html?id={quote(publication['public_id'])}"
        )
    if publication["published_local"]:
        publication["local_site_url"] = f"/api/public-site/viewer.html?id={quote(publication['public_id'])}"
    summary.update({
        "record": record,
        "difficulty_assessment": record.get("difficulty_assessment"),
        "problem": (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").exists() else "",
        "student_solution": (entry / "student-solution.md").read_text(encoding="utf-8")
        if (entry / "student-solution.md").exists()
        else "",
        "teacher_solution": (entry / "teacher-solution.md").read_text(encoding="utf-8")
        if (entry / "teacher-solution.md").exists()
        else ((entry / "solution.md").read_text(encoding="utf-8") if (entry / "solution.md").exists() else ""),
        "source_review_record": read_json(entry / "source-review.json", {}),
        "answer_review_record": read_json(entry / "answer-review.json", record.get("answer_review", {})),
        "analysis_request": read_json(entry / "analysis-request.json", {}),
        "w3_shadow": w3_shadow,
        "answer_digest": process_uploads.answer_digest(entry),
        "images": [
            f"/api/entry-file/{quote(entry.name)}/{quote(relative)}"
            for relative in record.get("source", {}).get("stored_files", [])
            if Path(relative).suffix.lower() in kb.IMAGE_EXTENSIONS and (entry / relative).exists()
        ],
        "delivery": read_json(entry / "delivery.json", {}),
        "downloads": delivery_files(entry),
        "delivery_guide": [
            {key: value for key, value in item.items() if key in {"name", "kind", "purpose", "recommended"}}
            for item in delivery_files(entry)
        ],
        "visualization": {
            **visualization,
            "preview_url": preview_url,
            "conversation": read_json(entry / "visualization-conversation.json", {"messages": []}),
        },
        "publication": publication,
        "publication_images": publication_images,
        "agent_configured": agent_available(),
        "agent_job": job_manager().public(latest_job) if latest_job else None,
    })
    return summary


class Handler(SimpleHTTPRequestHandler):
    server_version = "TeacherConsole/1.0"

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

    def json_response(self, value, status=HTTPStatus.OK):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def error_response(self, exc, status=HTTPStatus.BAD_REQUEST):
        self.json_response({"status": "error", "errors": [str(exc)]}, status)

    def read_body(self, limit):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > limit:
            raise ValueError("request body is too large")
        return self.rfile.read(length)

    def read_json_body(self):
        raw = self.read_body(MAX_JSON)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def require_local_action(self):
        if self.headers.get("X-Teacher-Console") != "1":
            raise PermissionError("missing local console action header")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/health":
                agent = agent_health()
                live_identity = runtime_identity(
                    project_root=PROJECT_ROOT, library=LIBRARY
                )
                return self.json_response({
                    "status": "ok",
                    "project": str(PROJECT_ROOT),
                    "agent_configured": agent["available"],
                    "agent": agent,
                    "runtime_identity": live_identity,
                    "runtime_identity_snapshot": SERVER_RUNTIME_IDENTITY_SNAPSHOT,
                    "runtime_stale": runtime_identity_is_stale(
                        live_identity, SERVER_RUNTIME_IDENTITY_SNAPSHOT
                    ),
                })
            if path == "/api/agent/providers":
                return self.json_response(agent_health())
            if path == "/api/agent/model-registry":
                return self.json_response(model_registry_settings())
            if path == "/api/agent/runtime":
                return self.json_response(runtime_settings_public(LIBRARY))
            if path == "/api/jobs":
                entry_id = parse_qs(parsed.query).get("entry_id", [""])[0]
                if not entry_id:
                    raise ValueError("entry_id is required")
                record = job_manager().latest_for_entry(unquote(entry_id))
                return self.json_response({"job": job_manager().public(record) if record else None})
            if path.startswith("/api/jobs/"):
                job_id = unquote(path.removeprefix("/api/jobs/")).strip("/")
                return self.json_response(job_manager().public(job_manager().get(job_id)))
            if path == "/api/retrieval-review":
                return self.json_response(retrieval_review_snapshot(LIBRARY))
            if path == "/api/entries":
                kb.init_library(LIBRARY)
                with FOLDER_LOCK:
                    groups = kb.sync_library_folders(LIBRARY)
                entries = [entry_summary(entry) for entry in reversed(list(kb.entry_dirs(LIBRARY)))]
                by_id = {entry["id"]: entry for entry in entries}
                folders = [
                    {
                        "name": group["name"],
                        "entries": [by_id[entry_id] for entry_id in group["entries"] if entry_id in by_id],
                    }
                    for group in groups
                ]
                return self.json_response({"entries": entries, "folders": folders})
            if path.startswith("/api/visualization/"):
                rest = path.removeprefix("/api/visualization/")
                entry_id, relative = rest.split("/", 1)
                if relative not in {"physics-simulator.html", "runtime-check.png"}:
                    raise FileNotFoundError(relative)
                target = safe_child(safe_entry(unquote(entry_id)) / process_uploads.VISUALIZATION_DIR, relative)
                return self.serve_file(target, inline=True)
            if path.startswith("/api/public-preview/"):
                rest = path.removeprefix("/api/public-preview/")
                entry_id, relative = rest.split("/", 1)
                target = safe_child(safe_entry(unquote(entry_id)) / public_site.DRAFT_DIR, relative)
                return self.serve_file(target, inline=True)
            if path.startswith("/api/public-site/"):
                relative = path.removeprefix("/api/public-site/")
                return self.serve_file(safe_child(PUBLIC_SITE, relative), inline=True)
            if path.startswith("/api/entries/"):
                entry_id = unquote(path.removeprefix("/api/entries/")).strip("/")
                return self.json_response(entry_detail(safe_entry(entry_id)))
            if path.startswith("/api/entry-file/"):
                rest = path.removeprefix("/api/entry-file/")
                entry_id, relative = rest.split("/", 1)
                return self.serve_file(safe_child(safe_entry(unquote(entry_id)), relative), inline=True)
            if path.startswith("/api/download/"):
                rest = path.removeprefix("/api/download/")
                entry_id, relative = rest.split("/", 1)
                entry = safe_entry(unquote(entry_id))
                delivery = read_json(entry / "delivery.json", {})
                output = Path(delivery.get("output", ""))
                if not output.exists():
                    raise FileNotFoundError("delivery output is unavailable")
                normalized = unquote(relative)
                if normalized not in DELIVERY_CATALOG or normalized not in delivery.get("files", []):
                    raise FileNotFoundError("file is not in the delivery allowlist")
                return self.serve_file(safe_child(output, normalized), inline=False)
            return self.serve_static(path)
        except FileNotFoundError as exc:
            self.error_response(exc, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self.error_response(exc)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            self.require_local_action()
            if path == "/api/upload":
                return self.handle_upload(parsed)
            if path == "/api/run-upload":
                return self.handle_run_upload()
            if path == "/api/retrieval-review/save":
                return self.json_response(save_retrieval_review(self.read_json_body(), LIBRARY))
            if path == "/api/folders/rename":
                data = self.read_json_body()
                with FOLDER_LOCK:
                    result = kb.rename_library_folder(
                        LIBRARY,
                        str(data.get("old_name", "")),
                        str(data.get("new_name", "")),
                    )
                return self.json_response(result)
            if path == "/api/agent/providers/probe":
                data = self.read_json_body()
                model_id = normalize_model_id(data.get("model_id"))
                return self.json_response(
                    AGENT_GATEWAY.probe(
                        str(data.get("provider", "")),
                        timeout_seconds=int(data.get("timeout_seconds", 120)),
                        allow_remote=remote_agent_allowed(),
                        model_config=model_config_for_task("gateway.probe", model_id, "auto"),
                        require_file_tools=data.get("require_file_tools") is True,
                    )
                )
            if path == "/api/agent/runtime":
                settings = save_runtime_settings(LIBRARY, self.read_json_body())
                AGENT_GATEWAY.invalidate_health()
                return self.json_response(settings)
            if path == "/api/agent/runtime/diagnose":
                data = self.read_json_body()
                if isinstance(data.get("settings"), dict):
                    save_runtime_settings(LIBRARY, data["settings"])
                    AGENT_GATEWAY.invalidate_health()
                snapshot = runtime_settings_public(LIBRARY)
                result = AGENT_GATEWAY.probe(
                    "codex",
                    timeout_seconds=int(data.get("timeout_seconds", 120)),
                    allow_remote=remote_agent_allowed(),
                    model_config={"provider": "codex"},
                )
                diagnosis = classify_runtime_probe(snapshot, result)
                snapshot = update_runtime_probe_result(LIBRARY, diagnosis)
                return self.json_response({
                    "runtime": snapshot,
                    "diagnosis": diagnosis,
                    "agent": result,
                })
            if path == "/api/agent/model-registry/test":
                data = self.read_json_body()
                model_id = normalize_model_id(data.get("model_id"))
                if isinstance(data.get("settings"), dict):
                    save_model_registry_settings(data["settings"])
                config = model_config_for_task("gateway.probe", model_id, "auto")
                result = AGENT_GATEWAY.probe(
                    str(config.get("provider", "")),
                    timeout_seconds=int(data.get("timeout_seconds", 120)),
                    allow_remote=remote_agent_allowed(),
                    model_config=config,
                    require_file_tools=str(config.get("provider", "")) in {"codex", "claude"},
                )
                settings = update_model_probe_result(model_id, result)
                return self.json_response({
                    "status": result.get("live_probe", {}).get("status", "failed"),
                    "agent": result,
                    "settings": settings,
                })
            if path == "/api/agent/model-registry":
                return self.json_response(save_model_registry_settings(self.read_json_body()))
            if path.startswith("/api/entries/"):
                rest = path.removeprefix("/api/entries/")
                entry_id, action = rest.split("/", 1)
                return self.handle_entry_action(safe_entry(unquote(entry_id)), action, self.read_json_body())
            self.error_response("unknown endpoint", HTTPStatus.NOT_FOUND)
        except PermissionError as exc:
            self.error_response(exc, HTTPStatus.FORBIDDEN)
        except FileExistsError as exc:
            self.error_response(exc, HTTPStatus.CONFLICT)
        except FileNotFoundError as exc:
            self.error_response(exc, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001
            self.error_response(exc)

    def handle_upload(self, parsed):
        query = parse_qs(parsed.query)
        raw_name = query.get("filename", [""])[0]
        name = Path(unquote(raw_name)).name
        if not name or Path(name).suffix.lower() not in ALLOWED_UPLOADS:
            raise ValueError("only JPG, PNG, WebP, HEIC, TIFF, BMP, or PDF files are accepted")
        data = self.read_body(MAX_UPLOAD)
        if not data:
            raise ValueError("uploaded file is empty")
        UPLOADS.mkdir(parents=True, exist_ok=True)
        target = UPLOADS / name
        if target.exists():
            target = UPLOADS / f"{target.stem}-{datetime.now():%Y%m%d-%H%M%S}{target.suffix.lower()}"
        target.write_bytes(data)
        self.json_response({"status": "uploaded", "filename": target.name, "size": len(data)})

    def handle_run_upload(self):
        data = self.read_json_body()
        filename = Path(str(data.get("filename", ""))).name
        source = safe_child(UPLOADS, filename)
        with LIBRARY_INDEX_LOCK, FOLDER_LOCK:
            report = process_uploads.start(
                LIBRARY,
                source,
                str(data.get("ocr", "auto")),
                None,
                str(data.get("subject", "高中物理")),
                "human",
                "unavailable",
                None,
                None,
            )
            for item in report.get("results", []):
                entry_id = str(item.get("entry_id", "")).strip()
                if item.get("status") != "ingested" or not entry_id:
                    continue
                try:
                    entry = Path(str(item.get("entry", ""))).resolve()
                    entry.relative_to((LIBRARY / "entries").resolve())
                    if not entry.is_dir() or entry.name != entry_id:
                        raise FileNotFoundError(entry_id)
                    outcome = run_visual_extract(
                        entry,
                        library=LIBRARY,
                        routing_tier="auto",
                        allow_remote=bool(
                            kb.load_json(LIBRARY / "config.json", {}).get("privacy", {}).get(
                                "allow_remote_visual_review", False
                            )
                        ),
                        gateway=AGENT_GATEWAY,
                    )
                    item["visual_extract_outcome"] = outcome
                    if outcome.get("status") != "completed":
                        item["source_review"] = {
                            "status": "needs-review",
                            "method": "registry-visual-extract",
                            "visual_extract_error": outcome.get("message", ""),
                            "failure_type": outcome.get("failure_type", ""),
                        }
                except Exception as exc:  # noqa: BLE001 - preserve human gate
                    item["visual_extract_error"] = str(exc)[:500]
            kb.rebuild_index(LIBRARY)
        # Queue OCR cleanup only after the upload transaction releases the
        # library/folder locks. A source.clean worker may promote entry files
        # immediately, so starting it inside the locked section risks coupling
        # Agent latency and index writes to the synchronous upload request.
        for item in report.get("results", []):
            entry_id = str(item.get("entry_id", "")).strip()
            if item.get("status") != "ingested" or not entry_id:
                continue
            clean_data = {"routing_tier": "economy"}
            try:
                # The ingest report is the source of truth for entries created
                # by this request. Validate its internal path instead of doing
                # a second catalog lookup that can race folder/index refreshes.
                entry = Path(str(item.get("entry", ""))).resolve()
                entry.relative_to((LIBRARY / "entries").resolve())
                if not entry.is_dir() or entry.name != entry_id:
                    raise FileNotFoundError(entry_id)
                model_id = resolve_model_id_for_task("source.clean", "economy", None)
                model_config_for_task("source.clean", model_id, "economy")
                item["source_clean"] = queue_agent_job(
                    "source.clean",
                    entry,
                    lambda entry=entry, clean_data=clean_data: self.run_source_clean(entry, clean_data),
                    routing_tier="economy",
                    model_id=model_id,
                )
            except Exception as exc:  # noqa: BLE001 - upload must degrade to human review
                item["source_clean"] = {
                    "status": "not-started",
                    "errors": [f"自动整理题干未启动：{str(exc)[:500]}"],
                }
        self.json_response(report)

    def handle_entry_action(self, entry: Path, action: str, data: dict):
        with visualization_lock(entry.name):
            return self._handle_entry_action_locked(entry, action, data)

    def _handle_entry_action_locked(self, entry: Path, action: str, data: dict):
        reviewer = str(data.get("reviewer", "teacher"))
        note = str(data.get("note", ""))
        active = job_manager().active_for_entry(entry.name)
        if active:
            return self.json_response(
                {"status": "blocked", "errors": ["这道题的 Agent 任务尚未结束"], "job": job_manager().public(active)},
                HTTPStatus.CONFLICT,
            )
        if action == "rename-entry":
            new_title = str(data.get("title", "")).strip()
            if not new_title:
                raise ValueError("标题不能为空")
            if len(new_title) > 120:
                raise ValueError("标题过长（最多 120 字符）")
            record = kb.load_json(entry / "record.json", {})
            record["title"] = new_title
            record["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            kb.write_json(entry / "record.json", record)
            with LIBRARY_INDEX_LOCK:
                kb.rebuild_index(LIBRARY)
            result = {"status": "renamed", "title": new_title}
        elif action == "approve-source":
            problem = str(data.get("problem", ""))
            if len(problem.strip()) < 30:
                raise ValueError("正式题干过短")
            kb.write_text(entry / "problem.md", problem)
            with LIBRARY_INDEX_LOCK:
                result = process_uploads.approve_source(LIBRARY, entry.name, reviewer, note)
        elif action == "source-clean":
            tier = source_clean_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("source.clean", tier, raw_model_id)
            model_config = model_config_for_task("source.clean", model_id, tier)
            result = queue_agent_job(
                "source.clean", entry, lambda: self.run_source_clean(entry, data), routing_tier=tier, model_id=model_id
            )
        elif action == "analyze":
            tier = normalize_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("analysis.generate", tier, raw_model_id)
            model_config_for_task("analysis.generate", model_id, tier)
            result = queue_agent_job(
                "analysis.generate",
                entry,
                lambda: self.run_adaptive_analysis(entry, data),
                routing_tier=tier,
                model_id=model_id,
            )
        elif action == "analyze-w3-shadow":
            tier = normalize_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("analysis.generate", tier, raw_model_id)
            model_config_for_task("analysis.generate", model_id, tier)
            result = queue_agent_job(
                "analysis.generate",
                entry,
                lambda: self.run_w3_shadow_analysis(entry, data),
                routing_tier=tier,
                model_id=model_id,
            )
        elif action == "save-answer":
            if process_uploads.pipeline_state(entry)["state"] == "needs-source-review":
                result = {"status": "blocked", "errors": ["请先确认正式题干，再编辑解析"]}
            else:
                result = self.save_answer(entry, data)
        elif action == "save-difficulty-assessment":
            result = save_difficulty_assessment(entry, data)
        elif action == "refresh-difficulty-assessment":
            result = {"status": "refreshed", "difficulty_assessment": assess_entry_difficulty(entry, force=True)}
        elif action == "route-preview":
            tier = normalize_routing_tier(data.get("routing_tier"))
            requested = data.get("model_id")
            try:
                model_id = resolve_model_id_for_task("analysis.generate", tier, requested)
                config = model_config_for_task("analysis.generate", model_id, tier)
            except Exception as exc:  # noqa: BLE001 - preview must not guess
                result = {
                    "schema": "wuli.route-preview.v1",
                    "kind": "analysis.generate",
                    "status": "blocked",
                    "error": str(exc)[:300],
                }
                return self.json_response(result, status=200)
            from deadline_budget import build_deadline_budget
            from route_snapshot import build_route_execution_plan

            core_config = kb.load_json(
                LIBRARY / "config" / "analysis-production-routing.json", {}
            )
            w3r_config, _ = analysis_routing.normalize_w3r_config(
                kb.load_json(LIBRARY / "config" / "w3r-production-routing.json", {})
            )
            task_deadline = float(core_config.get("max_latency_seconds", 90))
            budget = build_deadline_budget(task_deadline=task_deadline)
            result = {
                "schema": "wuli.route-preview.v1",
                "kind": "analysis.generate",
                "status": "ready",
                "resolved_model_id": str(config.get("id", "")),
                "provider": str(config.get("provider", "")),
                "upstream_model": str(config.get("model", "")),
                "qualification": analysis_qualification_public(str(config.get("id", ""))),
                "deadline_budget": budget.to_dict(),
                "route_config_digest": route_config_digest(LIBRARY),
                # A2.1/A3.1: the planned solver route and renderer mode are
                # explicit so the teacher never infers W3/W3R from button copy.
                "route_execution_plan": build_route_execution_plan(
                    library=LIBRARY,
                    core_config=core_config,
                    w3r_config=w3r_config,
                ),
            }
        elif action == "build-diagram":
            tier = normalize_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            from diagram_application import build_diagram

            model_config = None
            try:
                model_id = resolve_model_id_for_task("analysis.generate", tier, raw_model_id)
                model_config = model_config_for_task("analysis.generate", model_id, tier)
            except Exception as exc:  # noqa: BLE001 - diagram can still run without config
                result = {"status": "blocked", "errors": [f"模型路由解析失败：{exc}"]}
                return self.json_response(result, status=200)
            with LIBRARY_INDEX_LOCK:
                result = build_diagram(
                    entry,
                    library=LIBRARY,
                    routing_tier=tier,
                    model_config=model_config,
                )
        elif action == "approve-answer":
            agent_diff = _compute_agent_diff(entry)
            with LIBRARY_INDEX_LOCK:
                result = process_uploads.approve_answer(LIBRARY, entry.name, reviewer, note, agent_diff=agent_diff)
            # Clean up baseline snapshot after approval — diff is now stored in answer-review.json
            if result.get("status") == "approved":
                baseline_dir = entry / ".agent-baseline"
                if baseline_dir.exists():
                    shutil.rmtree(baseline_dir)
        elif action == "request-revision":
            tier = normalize_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("answer.revise", tier, raw_model_id)
            if raw_model_id is not None:
                model_config_for_task("answer.revise", model_id, tier)
            result = queue_agent_job(
                "answer.revise",
                entry,
                lambda: self.run_answer_revision(entry, data),
                routing_tier=tier,
                model_id=model_id,
            )
        elif action == "build-visualization":
            current_state = process_uploads.pipeline_state(entry)
            if current_state["state"] in {"needs-source-review", "needs-analysis-and-answer", "needs-answer-review"}:
                result = {
                    "status": "blocked",
                    "errors": ["请先生成并批准解析，再构建动态可视化"],
                    "state": current_state,
                }
            elif not (entry / "physics-model.json").exists():
                request = {
                    "message": str(data.get("message", "")).strip() or "我想为这道题生成一个可交互的可视化结果。",
                    "base_digest": str(data.get("base_digest", "")),
                }
                request["routing_tier"] = normalize_routing_tier(data.get("routing_tier"))
                raw_model_id = data.get("model_id")
                request["model_id"] = resolve_model_id_for_task(
                    "visualization.model", request["routing_tier"], raw_model_id
                )
                if raw_model_id is not None:
                    model_config_for_task("visualization.model", request["model_id"], request["routing_tier"])
                result = queue_agent_job(
                    "visualization.model",
                    entry,
                    lambda: self.run_visualization_chat(entry, request),
                    routing_tier=request["routing_tier"],
                    model_id=request["model_id"],
                )
            else:
                with visualization_lock(entry.name):
                    result = process_uploads.prepare_visualization(
                        LIBRARY,
                        entry.name,
                        str(data.get("runtime_check", "auto")),
                        bool(data.get("force", False)),
                    )
        elif action == "approve-visualization":
            current_state = process_uploads.pipeline_state(entry)
            if current_state["state"] in {"needs-source-review", "needs-analysis-and-answer", "needs-answer-review"}:
                result = {"status": "blocked", "errors": ["请先批准当前解析，再批准动态可视化"], "state": current_state}
            else:
                result = process_uploads.approve_visualization(LIBRARY, entry.name, reviewer, note)
        elif action == "visualization-chat":
            tier = normalize_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("visualization.model", tier, raw_model_id)
            if raw_model_id is not None:
                model_config_for_task("visualization.model", model_id, tier)
            result = queue_agent_job(
                "visualization.model",
                entry,
                lambda: self.run_visualization_chat(entry, data),
                routing_tier=tier,
                model_id=model_id,
            )
        elif action == "clear-visualization-chat":
            result = self.clear_visualization_chat(entry)
        elif action == "prepare-publication":
            with PUBLICATION_LOCK:
                result = public_site.prepare_publication(LIBRARY, entry.name, PUBLIC_SITE)
        elif action == "save-publication-images":
            if data.get("privacy_confirmed") is not True:
                result = {"status": "blocked", "errors": ["请先确认裁剪范围和全部遮挡区域"]}
            else:
                with PUBLICATION_LOCK:
                    result = public_site.save_public_images(entry, data.get("pages", []), reviewer, note)
        elif action == "publish-publication":
            if data.get("privacy_confirmed") is not True:
                result = {"status": "blocked", "errors": ["请先确认公开页面不包含学生隐私或教师内部材料"]}
            else:
                with PUBLICATION_LOCK:
                    result = public_site.publish_prepared(LIBRARY, entry.name, reviewer, note, PUBLIC_SITE)
        elif action == "finish":
            with LIBRARY_INDEX_LOCK:
                result = process_uploads.finish(LIBRARY, entry.name, None, str(data.get("simulator", "auto")))
        else:
            raise ValueError(f"unknown entry action: {action}")
        status = (
            HTTPStatus.CONFLICT
            if result.get("status") == "blocked"
            else (HTTPStatus.ACCEPTED if result.get("status") == "queued" else HTTPStatus.OK)
        )
        self.json_response(result, status)

    def save_answer(self, entry: Path, data: dict):
        return save_answer_entry(LIBRARY, entry, data)

    def run_source_clean(self, entry: Path, data: dict):
        with TraceContext() as ctx:
            ctx.info("stage=source.clean entry_id=%s status=started", entry.name)
            routing_tier = source_clean_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("source.clean", routing_tier, raw_model_id)
            model_config = model_config_for_task("source.clean", model_id, routing_tier)
            request = {
                "schema_version": 1,
                "entry_id": entry.name,
                "status": "requested",
                "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "routing_tier": routing_tier,
                "model_id": model_id,
                "model_display_name": model_config.get("display_name") if model_config else "",
            }
            kb.write_json(entry / "source-clean-request.json", request)
            gateway = run_agent_gateway(
                entry,
                source_clean_task(entry, routing_tier, model_config),
                lambda staging, changed: validate_source_clean_candidate(staging, changed, entry),
            )
            if gateway["status"] == "unavailable":
                request["status"] = "awaiting-agent"
                request["message"] = "没有可用的 Agent provider；请求已保留，可在配置 Gateway 后重试。"
                request["gateway"] = gateway
                archive = archive_agent_result(
                    entry,
                    "source.clean",
                    request,
                    gateway,
                    summary="Agent 整理题干候选",
                )
                request["archive_event_id"] = archive.get("event_id")
                kb.write_json(entry / "source-clean-request.json", request)
                ctx.info("stage=source.clean entry_id=%s status=awaiting-agent", entry.name)
                return request
            succeeded = gateway["status"] == "completed"
            request.update({
                "status": "completed" if succeeded else "failed",
                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "provider": gateway.get("provider"),
                "returncode": gateway.get("returncode"),
                "stdout": _sanitize_output(gateway.get("stdout", "")),
                "stderr": _sanitize_output(gateway.get("stderr", "")),
                "changed_files": gateway.get("changed_files", []),
                "unauthorized_changes": gateway.get("unauthorized_changes", []),
                "validation_errors": gateway.get("validation_errors", []),
                "attempts": gateway.get("attempts", []),
                **gateway_routing_fields(gateway),
            })
            if not succeeded:
                request["message"] = gateway_failure_detail(gateway, "Agent 未能整理题干文字")
            archive = archive_agent_result(
                entry,
                "source.clean",
                request,
                gateway,
                summary="Agent 整理题干候选",
            )
            request["archive_event_id"] = archive.get("event_id")
            kb.write_json(entry / "source-clean-request.json", request)
            if succeeded:
                SOURCE_CLEAN_INDEX_DEBOUNCER.schedule()
            ctx.info(
                "stage=source.clean entry_id=%s status=%s provider=%s",
                entry.name,
                "completed" if succeeded else "failed",
                gateway.get("provider"),
            )
            return request

    def _persist_adaptive_routing(
        self,
        entry: Path,
        result: dict,
        decision: dict,
        *,
        fallback: dict | None = None,
    ) -> dict:
        if not isinstance(result, dict):
            return result
        fallback_target = (
            str(fallback.get("to", "")).strip()
            if isinstance(fallback, dict)
            else ""
        )
        result["adaptive_routing"] = {
            **decision,
            "selected_route": (
                fallback_target
                if fallback_target in {"w2", "w3", "core", "legacy-adaptive"}
                else decision.get("route", "legacy-adaptive")
            ),
            "fallback": fallback,
        }
        kb.write_json(entry / "analysis-request.json", result)
        pipeline = kb.load_json(
            entry / "pipeline.json",
            {"schema_version": 1, "entry_id": entry.name},
        )
        pipeline["analysis_request"] = result
        kb.write_json(entry / "pipeline.json", pipeline)
        return result

    def _promote_w3_candidate(
        self,
        entry: Path,
        data: dict,
        decision: dict,
        w3_request: dict,
    ) -> tuple[dict | None, list[str]]:
        report = w3_request.get("report", {})
        w3r_config = kb.load_json(
            LIBRARY / "config" / W3R_ROUTING_CONFIG_NAME,
            {},
        )
        renderer_selection = analysis_routing.select_renderer(
            report,
            entry_id=entry.name,
            config=w3r_config,
        )
        renderer_review = analysis_routing.w3r_teacher_review_summary(
            report, renderer_selection
        )
        files = analysis_routing.candidate_files(
            report,
            entry_id=entry.name,
            w3r_config=w3r_config,
            selection=renderer_selection,
            diagram_plugin_id=str(data.get("diagram_plugin_id", "")).strip(),
        )
        record = kb.load_json(entry / "record.json", {})
        if not record.get("knowledge_points"):
            blueprint = (
                report.get("blueprint", {})
                if isinstance(report.get("blueprint"), dict)
                else {}
            )
            labels = [
                str(item.get("label", "")).strip()
                for item in blueprint.get("physical_stages", [])
                if isinstance(item, dict) and str(item.get("label", "")).strip()
            ]
            if not labels:
                labels = [
                    str(item.get("statement", "")).strip()
                    for item in blueprint.get("targets", [])
                    if isinstance(item, dict)
                    and str(item.get("statement", "")).strip()
                ]
            record["knowledge_points"] = list(dict.fromkeys(labels))[:8] or [
                "竞赛物理综合建模"
            ]
            if not record.get("error_types"):
                record["error_types"] = ["竞赛综合题"]
            files["record.json"] = (
                json.dumps(record, ensure_ascii=False, indent=2) + "\n"
            )
        routing_tier = normalize_routing_tier(data.get("routing_tier"))
        model_id = resolve_model_id_for_task(
            "analysis.generate", routing_tier, data.get("model_id")
        )
        model_config = None
        changed_files = sorted(files)
        with tempfile.TemporaryDirectory(
            prefix=".w3-production-candidate-",
            dir=entry.parent,
        ) as temporary:
            staging = Path(temporary) / entry.name
            shutil.copytree(entry, staging)
            for relative, content in files.items():
                kb.write_text(staging / relative, content)
            if (
                (staging / "visual-facts.json").is_file()
                and not str(data.get("diagram_plugin_id", "")).strip()
            ):
                model_config = model_config_for_task(
                    "analysis.generate",
                    model_id,
                    routing_tier,
                )
                diagram_gateway = run_physics_diagram_gateway(
                    staging,
                    routing_tier=routing_tier,
                    model_config=model_config,
                    canonical_entry=staging,
                )
                if diagram_gateway.get("status") != "completed":
                    return None, [
                        *diagram_gateway.get("validation_errors", []),
                        gateway_failure_detail(
                            diagram_gateway,
                            "W3 物理图原子任务未形成可信候选",
                        ),
                    ]
                for relative in (
                    physics_diagram.SCENE_PATH,
                    physics_diagram.GATE_PATH,
                    physics_diagram.SVG_PATH,
                    physics_diagram.PROVENANCE_PATH,
                ):
                    files[relative] = (staging / relative).read_text(encoding="utf-8")
                changed_files = sorted(files)
            validation_errors = validate_answer_candidate(
                staging, changed_files, entry
            )
        if validation_errors:
            return None, validation_errors

        originals = {
            relative: (entry / relative).read_bytes()
            if (entry / relative).is_file()
            else None
            for relative in changed_files
        }
        try:
            for relative, content in files.items():
                kb.write_text(entry / relative, content)
        except Exception:
            for relative, content in originals.items():
                target = entry / relative
                if content is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.write_bytes(content)
            raise

        marked = mark_answer_needs_review(
            LIBRARY,
            entry,
            "W3 自适应路由已生成结构化解析，等待教师复核",
        )
        _save_agent_baseline(
            entry, changed_files, task_type="analysis.generate.w3-adaptive"
        )
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        request = {
            "schema_version": 1,
            "entry_id": entry.name,
            "status": "completed",
            "requested_at": w3_request.get("requested_at", now),
            "completed_at": now,
            "instruction": str(
                data.get(
                    "instruction",
                    "生成分层解析和解释图；本阶段不生成交互仿真",
                )
            ),
            "routing_tier": routing_tier,
            "model_id": model_id,
            "provider": "w3-adaptive-orchestrator",
            "changed_files": changed_files,
            "validation_errors": [],
            "stages": [
                *w3_request.get("stages", []),
                {"name": "candidate-validation", "status": "completed"},
                {"name": "canonical-promotion", "status": "completed"},
            ],
            "resulting_state": marked["state"]["state"],
            "difficulty_assessment": assess_entry_difficulty(entry),
            "adaptive_routing": {
                **decision,
                "selected_route": "w3",
                "fallback": None,
                "renderer": renderer_selection,
            },
            "w3_report_file": "w3-shadow-report.json",
            "w3r_review": renderer_review,
        }
        gateway = {
            "status": "completed",
            "provider": "w3-adaptive-orchestrator",
            "model_id": model_id,
            "changed_files": changed_files,
            "validation_errors": [],
            "attempts": w3_request.get("stages", []),
        }
        archive = archive_agent_result(
            entry,
            "analysis.generate",
            request,
            gateway,
            summary="W3 自适应路由生成分层解析候选",
        )
        request["archive_event_id"] = archive.get("event_id")
        kb.write_json(entry / "analysis-request.json", request)
        pipeline = kb.load_json(
            entry / "pipeline.json",
            {"schema_version": 1, "entry_id": entry.name},
        )
        pipeline.update(
            {"state": marked["state"]["state"], "analysis_request": request}
        )
        kb.write_json(entry / "pipeline.json", pipeline)
        return request, []

    def run_adaptive_analysis(self, entry: Path, data: dict):
        started = time.monotonic()
        core_config, core_config_errors = core_analysis.normalize_routing_config(
            kb.load_json(
                LIBRARY / "config" / CORE_ROUTING_CONFIG_NAME,
                {},
            )
        )
        if core_config["mode"] == "core-first":
            result = self.run_core_analysis(entry, data, core_config=core_config)
            elapsed = round(time.monotonic() - started, 4)
            return self._persist_adaptive_routing(
                entry,
                result,
                {
                    "schema_version": 1,
                    "policy_version": core_analysis.ROUTING_POLICY,
                    "mode": "core-first",
                    "route": "core",
                    "reason": "unified-core-first-default",
                    "config_errors": core_config_errors,
                    "legacy_rollback": "legacy-adaptive",
                    "limits": {
                        "max_latency_seconds": core_config["max_latency_seconds"],
                        "max_agent_calls": 1,
                    },
                    "observed_metrics": {
                        "selected_route": "core",
                        "latency_seconds": elapsed,
                        "agent_call_count": 1,
                        "fallback_used": False,
                    },
                },
            )
        problem = (
            (entry / "problem.md").read_text(encoding="utf-8")
            if (entry / "problem.md").is_file()
            else ""
        )
        decision = analysis_routing.decide(
            problem,
            entry_id=entry.name,
            config=kb.load_json(W3_ROUTING_CONFIG_PATH, {}),
            has_physics_model=(entry / "physics-model.json").is_file(),
        )
        if decision["route"] != "w3":
            decision["observed_metrics"] = {
                "selected_route": "w2",
                "latency_seconds": 0.0,
                "agent_call_count": 1,
                "teacher_focus_count": 0,
                "fallback_used": False,
            }
            result = self.run_analysis(entry, data)
            decision["observed_metrics"]["latency_seconds"] = round(
                time.monotonic() - started, 4
            )
            return self._persist_adaptive_routing(
                entry, result, decision
            )

        w3_request = self.run_w3_shadow_analysis(entry, data)
        w3_elapsed = round(time.monotonic() - started, 4)
        w3_request["production_elapsed_seconds"] = w3_elapsed
        ready, readiness_errors = analysis_routing.production_readiness(
            w3_request, decision
        )
        report = (
            w3_request.get("report", {})
            if isinstance(w3_request.get("report"), dict)
            else {}
        )
        metrics = (
            report.get("metrics", {})
            if isinstance(report.get("metrics"), dict)
            else {}
        )
        decision["observed_metrics"] = {
            "selected_route": "w3",
            "latency_seconds": w3_elapsed,
            "agent_call_count": len(w3_request.get("stages", [])),
            "teacher_focus_count": int(metrics.get("teacher_focus_count", 0)),
            "fallback_used": not ready,
        }
        if ready:
            promoted, validation_errors = self._promote_w3_candidate(
                entry, data, decision, w3_request
            )
            if promoted is not None:
                return promoted
            readiness_errors.extend(
                f"candidate-validation: {item}" for item in validation_errors
            )
        if decision.get("w3_failure_policy") == "stop":
            decision["observed_metrics"]["fallback_used"] = False
            return self._persist_adaptive_routing(
                entry,
                w3_request,
                decision,
                fallback={
                    "from": "w3",
                    "to": "none",
                    "reason_codes": sorted(set(readiness_errors)),
                    "w3_status": str(w3_request.get("status", "failed")),
                    "policy": "stop-after-w3-failure",
                },
            )
        fallback = {
            "from": "w3",
            "to": "w2",
            "reason_codes": sorted(set(readiness_errors)),
            "w3_status": str(w3_request.get("status", "failed")),
        }
        decision["observed_metrics"]["fallback_used"] = True
        fallback_started = time.monotonic()
        result = self.run_analysis(entry, data)
        fallback["w2_latency_seconds"] = round(
            time.monotonic() - fallback_started, 4
        )
        fallback["w2_status"] = str(result.get("status", "failed"))
        decision["observed_metrics"]["latency_seconds"] = round(
            time.monotonic() - started, 4
        )
        decision["observed_metrics"]["selected_route"] = "w2"
        return self._persist_adaptive_routing(
            entry,
            result,
            decision,
            fallback=fallback,
        )

    def run_core_analysis(
        self,
        entry: Path,
        data: dict,
        *,
        core_config: dict | None = None,
    ):
        """Run one compact solve, one deterministic gate, and one pure renderer."""
        with TraceContext() as ctx:
            ctx.info("stage=core-analysis entry_id=%s status=started", entry.name)
            state = process_uploads.pipeline_state(entry)
            if state["state"] == "needs-source-review":
                return {
                    "status": "blocked",
                    "errors": ["请先对照原图批准正式题干"],
                    "state": state,
                }
            routing_tier = normalize_routing_tier(data.get("routing_tier"))
            model_id = resolve_model_id_for_task(
                "analysis.generate", routing_tier, data.get("model_id")
            )
            model_config = model_config_for_task(
                "analysis.generate", model_id, routing_tier
            )
            method_profile = teaching_method_policy.normalize_profile(
                data.get("method_profile", "high_school_standard")
            )
            problem = (entry / "problem.md").read_text(encoding="utf-8")
            brief = core_analysis.build_target_brief(
                problem,
                method_profile=method_profile,
                has_visual_facts=(entry / "visual-facts.json").is_file(),
                has_physics_model=(entry / "physics-model.json").is_file(),
            )
            instruction = str(
                data.get("instruction", "生成可判分、可复算的核心解答")
            )
            task = core_analysis_task(
                entry,
                instruction,
                brief,
                routing_tier,
                model_config,
            )
            config = core_config or core_analysis.DEFAULT_ROUTING
            task["timeout_seconds"] = int(config.get("max_latency_seconds", 90))
            request = {
                "schema_version": 1,
                "entry_id": entry.name,
                "status": "requested",
                "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "instruction": instruction,
                "routing_tier": routing_tier,
                "method_profile": method_profile,
                "model_id": model_id,
                "model_display_name": model_config.get("display_name") if model_config else "",
                "target_brief": brief,
            }
            kb.write_json(entry / "analysis-request.json", request)

            def validator(staging, changed):
                return validate_answer_candidate(
                    staging,
                    changed,
                    entry,
                    method_profile=method_profile,
                    allow_missing_explanatory_image=True,
                )

            gateway = run_agent_gateway(
                entry,
                task,
                validator,
                materializer=lambda staging, payload: core_analysis.materialize(
                    staging, payload, brief
                ),
                # A failed solve is not repeated under a different W2/W3 prompt.
                bounded_failure_repair=False,
            )
            completed = gateway.get("status") == "completed"
            if completed:
                marked = mark_answer_needs_review(
                    LIBRARY,
                    entry,
                    "核心求解已通过确定性门禁，等待教师/标准答案复核",
                )
                resulting_state = marked["state"]
                _save_agent_baseline(
                    entry,
                    gateway.get("changed_files", []),
                    task_type="analysis.generate",
                )
            else:
                resulting_state = process_uploads.pipeline_state(entry)
            request.update(
                {
                    "status": "completed" if completed else gateway.get("status", "failed"),
                    "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "provider": gateway.get("provider"),
                    "returncode": gateway.get("returncode"),
                    "stdout": _sanitize_output(gateway.get("stdout", "")),
                    "stderr": _sanitize_output(gateway.get("stderr", "")),
                    "changed_files": gateway.get("changed_files", []),
                    "unauthorized_changes": gateway.get("unauthorized_changes", []),
                    "validation_errors": gateway.get("validation_errors", []),
                    "attempts": gateway.get("attempts", []),
                    "stages": [
                        *analysis_artifacts.stage_records(gateway),
                        {
                            "name": "authoritative-review",
                            "status": "pending" if completed else "not-run",
                            "authority": "teacher-or-standard-answer",
                        },
                    ],
                    "resulting_state": resulting_state["state"],
                    "diagram_task": {
                        "status": "not-run",
                        "reason": "optional-post-answer-enhancement",
                    },
                    **gateway_routing_fields(gateway),
                }
            )
            if not completed:
                request["message"] = gateway_failure_detail(
                    gateway, "核心求解未通过；未触发旧 W2/W3 重复求解"
                )
            archive = archive_agent_result(
                entry,
                "analysis.generate",
                request,
                gateway,
                summary="统一核心求解候选",
            )
            request["archive_event_id"] = archive.get("event_id")
            kb.write_json(entry / "analysis-request.json", request)
            pipeline = kb.load_json(
                entry / "pipeline.json",
                {"schema_version": 1, "entry_id": entry.name},
            )
            pipeline.update(
                {"state": resulting_state["state"], "analysis_request": request}
            )
            kb.write_json(entry / "pipeline.json", pipeline)
            ctx.info(
                "stage=core-analysis entry_id=%s status=%s resulting_state=%s",
                entry.name,
                request["status"],
                resulting_state["state"],
            )
            return request

    def run_analysis(self, entry: Path, data: dict):
        with TraceContext() as ctx:
            ctx.info("stage=analysis entry_id=%s status=started", entry.name)
            routing_tier = normalize_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("analysis.generate", routing_tier, raw_model_id)
            model_config = model_config_for_task("analysis.generate", model_id, routing_tier)
            current_state = process_uploads.pipeline_state(entry)
            if current_state["state"] == "needs-source-review":
                return {"status": "blocked", "errors": ["请先对照原图批准正式题干"], "state": current_state}
            instruction = str(data.get("instruction", "生成分层解析和解释图；本阶段不生成交互仿真"))
            request = {
                "schema_version": 1,
                "entry_id": entry.name,
                "status": "requested",
                "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "instruction": instruction,
                "routing_tier": routing_tier,
                "model_id": model_id,
                "model_display_name": model_config.get("display_name") if model_config else "",
            }
            kb.write_json(entry / "analysis-request.json", request)
            task = analysis_task(entry, instruction, routing_tier, model_config)
            evidence_payload = task.get("context_payloads", {}).get(
                ".agent-context/knowledge-evidence.json",
                {},
            )
            evidence_digest = hashlib.sha256(
                json.dumps(
                    {
                        "knowledge_evidence": evidence_payload,
                        "visual_facts": kb.load_json(entry / "visual-facts.json", {}),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            fingerprint = analysis_artifacts.input_fingerprint(
                entry,
                instruction=instruction,
                model_id=model_id,
                routing_tier=routing_tier,
                evidence_digest=evidence_digest,
            )
            checkpoint = analysis_artifacts.load_generation_checkpoint(
                entry,
                fingerprint=fingerprint,
            )

            transactional_paths = {
                "record.json",
                "student-solution.md",
                "teacher-solution.md",
                "solution.md",
                physics_diagram.SCENE_PATH,
                physics_diagram.GATE_PATH,
                physics_diagram.SVG_PATH,
                physics_diagram.PROVENANCE_PATH,
            }
            originals = {
                relative: (entry / relative).read_bytes() if (entry / relative).is_file() else None
                for relative in transactional_paths
            }

            def rollback_composite_candidate():
                for relative, content in originals.items():
                    target = entry / relative
                    if content is None:
                        if target.exists():
                            target.unlink()
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(content)

            def validator(staging, changed):
                return validate_answer_candidate(
                    staging,
                    changed,
                    entry,
                    allow_pending_diagram=True,
                )

            if checkpoint is not None:
                gateway = AGENT_GATEWAY.replay_structured(
                    task,
                    checkpoint,
                    validator,
                    materializer=analysis_artifacts.materialize,
                )
            else:
                def materialize_with_checkpoint(staging, payload):
                    analysis_artifacts.save_generation_checkpoint(
                        entry,
                        fingerprint=fingerprint,
                        payload=payload,
                    )
                    return analysis_artifacts.materialize(staging, payload)

                gateway = run_agent_gateway(
                    entry,
                    task,
                    validator,
                    materializer=materialize_with_checkpoint,
                )
            if gateway["status"] == "unavailable":
                request["status"] = "awaiting-agent"
                request["message"] = "没有可用的 Agent provider；请求已保留，可在配置 Gateway 后重试。"
                request["gateway"] = gateway
                archive = archive_agent_result(
                    entry,
                    "analysis.generate",
                    request,
                    gateway,
                    summary="Agent 生成分层解析候选",
                )
                request["archive_event_id"] = archive.get("event_id")
                kb.write_json(entry / "analysis-request.json", request)
                ctx.info("stage=analysis entry_id=%s status=awaiting-agent", entry.name)
                return request

            diagram_gateway: dict = {"status": "not-run", "changed_files": []}
            if gateway["status"] == "completed":
                if (entry / "visual-facts.json").is_file():
                    diagram_gateway = run_physics_diagram_gateway(
                        entry,
                        routing_tier=routing_tier,
                        model_config=model_config,
                        canonical_entry=entry,
                    )
                elif originals[physics_diagram.SVG_PATH] is not None:
                    diagram_gateway = {
                        "status": "completed",
                        "changed_files": [],
                        "materialization": {
                            "stages": [{"name": "legacy-physics-diagram", "status": "preserved"}],
                        },
                    }
                else:
                    diagram_gateway = {
                        "status": "failed",
                        "changed_files": [],
                        "validation_errors": [
                            "缺少经来源复核的 visual-facts.json；物理图任务未运行，且不会回退成流程图"
                        ],
                    }

            succeeded = gateway["status"] == "completed" and diagram_gateway["status"] == "completed"
            if gateway["status"] == "completed" and not succeeded:
                rollback_composite_candidate()
            combined_changed = sorted(set(gateway.get("changed_files", [])) | set(diagram_gateway.get("changed_files", [])))
            if succeeded:
                analysis_artifacts.clear_generation_checkpoint(entry)
                request["difficulty_assessment"] = assess_entry_difficulty(entry)
                marked = mark_answer_needs_review(LIBRARY, entry, "Agent 已生成分层解析，等待教师复核")
                resulting_state = marked["state"]
                _save_agent_baseline(entry, combined_changed, task_type="analysis.generate")
            else:
                resulting_state = process_uploads.pipeline_state(entry)
            request.update({
                "status": "completed" if succeeded else "failed",
                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "provider": gateway.get("provider"),
                "returncode": gateway.get("returncode"),
                "stdout": _sanitize_output(gateway.get("stdout", "")),
                "stderr": _sanitize_output(gateway.get("stderr", "")),
                "changed_files": combined_changed,
                "unauthorized_changes": gateway.get("unauthorized_changes", []),
                "validation_errors": sorted(set(
                    gateway.get("validation_errors", []) + diagram_gateway.get("validation_errors", [])
                )),
                "attempts": gateway.get("attempts", []),
                "stages": [
                    *analysis_artifacts.stage_records(gateway),
                    *(
                        analysis_artifacts.stage_records(diagram_gateway)
                        if diagram_gateway.get("status") not in {"not-run"}
                        else []
                    ),
                    {
                        "name": "candidate-validation",
                        "status": "completed" if succeeded else "failed",
                    },
                    {
                        "name": "canonical-promotion",
                        "status": "completed" if succeeded else "not-run",
                    },
                ],
                "resulting_state": resulting_state["state"],
                **gateway_routing_fields(gateway),
            })
            request["diagram_task"] = {
                "kind": "diagram.scene",
                "status": diagram_gateway.get("status"),
                "changed_files": diagram_gateway.get("changed_files", []),
                "validation_errors": diagram_gateway.get("validation_errors", []),
                "repair": diagram_gateway.get("diagram_repair", {}),
                **gateway_routing_fields(diagram_gateway),
            }
            if not succeeded:
                failed_gateway = diagram_gateway if gateway.get("status") == "completed" else gateway
                request["message"] = gateway_failure_detail(
                    failed_gateway,
                    "Agent 未形成可复核的物理图与答案组合",
                )
            archive = archive_agent_result(
                entry,
                "analysis.generate",
                request,
                gateway,
                summary="Agent 生成分层解析候选",
            )
            request["archive_event_id"] = archive.get("event_id")
            kb.write_json(entry / "analysis-request.json", request)
            pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry.name})
            pipeline["state"] = resulting_state["state"]
            pipeline["analysis_request"] = request
            kb.write_json(entry / "pipeline.json", pipeline)
            ctx.info(
                "stage=analysis entry_id=%s status=%s provider=%s resulting_state=%s",
                entry.name,
                "completed" if succeeded else "failed",
                gateway.get("provider"),
                resulting_state["state"],
            )
            return request

    def run_w3_shadow_analysis(self, entry: Path, data: dict):
        """Run W3 in a private shadow artifact without changing the reviewed answer."""
        with TraceContext() as ctx:
            ctx.info("stage=w3-shadow entry_id=%s status=started", entry.name)
            state = process_uploads.pipeline_state(entry)
            if state["state"] == "needs-source-review":
                return {"status": "blocked", "errors": ["请先对照原图批准正式题干"], "state": state}
            routing_tier = normalize_routing_tier(data.get("routing_tier"))
            requested_model_id = data.get("model_id")
            claim_evidence_enabled = (
                correctness_policy.claim_evidence_shadow_enabled()
            )
            if claim_evidence_enabled and str(
                requested_model_id or "auto"
            ).strip() == "auto":
                model_id = resolve_model_id_for_task(
                    "analysis.generate", "expert", "auto"
                )
            else:
                model_id = resolve_model_id_for_task(
                    "analysis.generate", routing_tier, requested_model_id
                )
            model_config = model_config_for_task("analysis.generate", model_id, routing_tier)
            claim_verifier_model_id = ""
            claim_verifier_model_config = None
            if claim_evidence_enabled:
                claim_verifier_model_id = resolve_model_id_for_task(
                    "claim.verify", "auto", "auto"
                )
                claim_verifier_model_config = model_config_for_task(
                    "claim.verify", claim_verifier_model_id, "auto"
                )
                solver_provider = str(
                    (model_config or {}).get("provider", "")
                ).strip()
                verifier_provider = str(
                    (claim_verifier_model_config or {}).get("provider", "")
                ).strip()
                if solver_provider not in {"claude", "openai-compatible"} or verifier_provider not in {"claude", "openai-compatible"}:
                    raise ValueError(
                        "W3 Claim Evidence 需要 claude 或 openai-compatible provider；"
                        f"当前 solver={solver_provider} verifier={verifier_provider}"
                    )
                solver_model = str((model_config or {}).get("model") or model_id)
                verifier_model = str((claim_verifier_model_config or {}).get("model") or claim_verifier_model_id)
                if solver_model == verifier_model:
                    raise ValueError(
                        "W3 求解器与 claim verifier 必须使用不同模型身份。"
                        f"当前均为 {solver_model}"
                    )
            problem = (entry / "problem.md").read_text(encoding="utf-8")
            method_profile = str(
                data.get("method_profile", "high_school_standard")
            ).strip()
            stage_telemetry: list[dict] = []
            pending_claim_checkpoints: list[tuple[Path, dict]] = []
            pending_claim_checkpoint_lock = threading.Lock()

            def flush_pending_claim_checkpoints() -> None:
                with pending_claim_checkpoint_lock:
                    pending = list(pending_claim_checkpoints)
                    pending_claim_checkpoints.clear()
                for checkpoint_path, checkpoint_payload in pending:
                    kb.write_json(checkpoint_path, checkpoint_payload)

            def run_stage(stage: str, context: dict) -> dict:
                stage_started = time.monotonic()
                batch_index = context.get("batch_index")
                if stage == "decompose":
                    contract = problem_decomposition.output_contract()
                    normalizer = problem_decomposition.normalize_payload
                    instruction = "只拆解当前题目的物理过程、推理过程、检索需求和校验义务。"
                elif stage in {"solver-a", "solver-b"}:
                    contract = solution_reasoning.output_contract(role=stage)
                    blueprint = context["blueprint"]
                    normalizer = lambda payload: solution_reasoning.normalize_solution(
                        payload,
                        blueprint,
                        require_stage_interfaces=(stage == "solver-a"),
                    )
                    instruction = (
                        "独立求解指定高风险目标，不得读取另一求解器的输出。"
                        if stage == "solver-b"
                        else "根据双层蓝图与证据集形成可复算的结构化结论。"
                    )
                elif stage == "verifier":
                    expected = set(context.get("target_ids", []))
                    contract = solution_verification.output_contract()
                    normalizer = lambda payload: solution_verification.normalize_audit(
                        payload, expected
                    )
                    instruction = "仅对指定目标独立复算；不得读取历史答案正文。"
                    context = {
                        **context,
                        "evidence": solution_verification.verification_evidence_view(
                            context.get("evidence", {})
                        ),
                    }
                elif stage == "adjudicator":
                    expected = set(context.get("target_ids", []))
                    contract = solution_reasoning.adjudication_output_contract()
                    normalizer = lambda payload: solution_reasoning.normalize_adjudication_with_verified_equivalence(
                        payload,
                        expected,
                        solver_a=context.get("solver_a", {}),
                        verifier=context.get("verifier"),
                    )
                    instruction = "依据证据与可复算关系仲裁冲突，不得投票。"
                elif stage == "claim-verifier":
                    expected = {
                        str(key): int(value)
                        for key, value in context.get(
                            "expected_claim_versions", {}
                        ).items()
                    }
                    contract = solution_verification.claim_output_contract()
                    normalizer = (
                        lambda payload: solution_verification.normalize_claim_audit(
                            payload, expected
                        )
                    )
                    instruction = (
                        "仅审计最小 Claim 快照；不得读取 Solver 身份、完整答案或历史答案。"
                    )
                    context = {
                        "verification_view": context.get(
                            "verification_view", {}
                        )
                    }
                else:
                    raise ValueError(f"unknown W3 stage: {stage}")

                stage_model_id = (
                    claim_verifier_model_id
                    if stage == "claim-verifier"
                    else model_id
                )
                stage_model_config = (
                    claim_verifier_model_config
                    if stage == "claim-verifier"
                    else model_config
                )
                stage_routing_tier = (
                    "auto" if stage == "claim-verifier" else routing_tier
                )
                payloads = {
                    f".agent-context/w3-{name}.json": value
                    for name, value in context.items()
                    if name != "problem" and isinstance(value, (dict, list))
                }
                task = w3_stage_task(
                    entry,
                    stage,
                    instruction,
                    contract,
                    payloads,
                    routing_tier=stage_routing_tier,
                    model_config=stage_model_config,
                )
                checkpoint_digest = w3_stage_checkpoint_digest(
                    stage=stage,
                    problem=problem,
                    context=context,
                    contract_name=str(contract.get("name", "")),
                    model_id=stage_model_id,
                    routing_tier=stage_routing_tier,
                )
                checkpoint_path = (
                    entry / ".cache" / "w3-shadow" / f"{stage}-{checkpoint_digest}.json"
                )
                replayed = replay_w3_stage_checkpoint(
                    checkpoint_path,
                    normalizer,
                    include_runtime_identity=claim_evidence_enabled,
                )
                if replayed is not None:
                    stage_telemetry.append({
                        "stage": stage,
                        "status": "completed",
                        "provider": "checkpoint",
                        "model_id": stage_model_id,
                        "usage": {},
                        "failure_type": "",
                        "message": "复用同输入、同契约的 W3 结构化检查点。",
                        **summarize_w3_stage_timing(
                            {"attempts": []},
                            elapsed_seconds=time.monotonic() - stage_started,
                        ),
                        **(
                            {"batch_index": batch_index}
                            if stage == "claim-verifier"
                            and isinstance(batch_index, int)
                            else {}
                        ),
                    })
                    return replayed
                def materializer(_staging, payload):
                    domain_payload = {
                        key: value
                        for key, value in payload.items()
                        if key
                        not in {
                            "model",
                            "model_tier",
                            "requested_tier",
                            "routing_notice",
                            "usage",
                        }
                    }
                    return {"payload": normalizer(domain_payload)}

                gateway = run_agent_gateway(
                    entry,
                    task,
                    None,
                    materializer=materializer,
                )
                telemetry = {
                    "stage": stage,
                    "status": gateway.get("status"),
                    "provider": gateway.get("provider"),
                    "model_id": gateway.get("model_id", stage_model_id),
                    "usage": gateway.get("usage", {}),
                    "failure_type": gateway.get("failure_type", ""),
                    "message": str(gateway.get("message", ""))[:500],
                    **summarize_w3_stage_timing(
                        gateway,
                        elapsed_seconds=time.monotonic() - stage_started,
                    ),
                    **(
                        {"batch_index": batch_index}
                        if stage == "claim-verifier"
                        and isinstance(batch_index, int)
                        else {}
                    ),
                }
                if gateway.get("status") != "completed":
                    telemetry["stderr"] = _sanitize_output(gateway.get("stderr", ""))
                    attempts = gateway.get("attempts", [])
                    last_attempt = (
                        attempts[-1]
                        if isinstance(attempts, list)
                        and attempts
                        and isinstance(attempts[-1], dict)
                        else {}
                    )
                    telemetry["adapter_error"] = _sanitize_output(
                        str(last_attempt.get("error", ""))
                    )[:1000]
                    telemetry["adapter_output"] = _sanitize_output(
                        str(last_attempt.get("stdout", ""))
                    )[-4000:]
                stage_telemetry.append(telemetry)
                if gateway.get("status") != "completed":
                    raise RuntimeError(gateway_failure_detail(gateway, f"W3 {stage} 失败"))
                payload = gateway.get("materialization", {}).get("payload")
                if not isinstance(payload, dict):
                    raise RuntimeError(f"W3 {stage} 未返回规范化结构")
                runtime_identity = {
                    "model_id": (
                        gateway.get("model")
                        or gateway.get("model_id")
                        or stage_model_id
                    ),
                    "provider": gateway.get("provider", ""),
                    "context_isolated": stage == "claim-verifier",
                }
                checkpoint_payload = {
                    "schema_version": 1,
                    "status": "completed",
                    "stage": stage,
                    "contract": contract.get("name"),
                    "payload": payload,
                    "runtime_identity": runtime_identity,
                }
                if stage == "claim-verifier":
                    # Claim batches may run concurrently. Writing one validated
                    # checkpoint while a sibling Gateway transaction is still
                    # comparing its canonical baseline creates a false
                    # canonical_changed conflict. Persist them as one
                    # post-batch commit without weakening that guard.
                    with pending_claim_checkpoint_lock:
                        pending_claim_checkpoints.append(
                            (checkpoint_path, checkpoint_payload)
                        )
                else:
                    kb.write_json(checkpoint_path, checkpoint_payload)
                if claim_evidence_enabled:
                    return {**payload, "_runtime_identity": runtime_identity}
                return payload

            def build_evidence(blueprint: dict) -> dict:
                from knowledge_store import build_blueprint_evidence

                return build_blueprint_evidence(
                    LIBRARY,
                    entry.name,
                    blueprint,
                    task_type="analysis.generate",
                    default_need_limit=3,
                    max_need_limit=5,
                    top_k=4 if routing_tier != "economy" else 2,
                    char_budget=9000 if routing_tier == "expert" else 8000,
                )

            request = {
                "schema_version": 1,
                "kind": "w3-shadow-analysis",
                "entry_id": entry.name,
                "status": "running",
                "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "routing_tier": routing_tier,
                "model_id": model_id,
                "claim_verifier_model_id": claim_verifier_model_id,
                "method_profile": method_profile,
                "baseline": "current-teacher-reviewed-answer",
                "canonical_answer_changed": False,
            }
            try:
                report = w3_pipeline.run_shadow(
                    problem,
                    stage_runner=run_stage,
                    evidence_builder=build_evidence,
                    has_physics_model=(entry / "physics-model.json").is_file(),
                    claim_evidence_shadow_enabled=claim_evidence_enabled,
                    method_profile=method_profile,
                )
                flush_pending_claim_checkpoints()
                claim_stages = sorted(
                    (
                        item
                        for item in stage_telemetry
                        if item.get("stage") == "claim-verifier"
                    ),
                    key=lambda item: int(item.get("batch_index", 0)),
                )
                if claim_stages:
                    stage_telemetry[:] = [
                        item
                        for item in stage_telemetry
                        if item.get("stage") != "claim-verifier"
                    ] + claim_stages
                request.update({
                    "status": "completed",
                    "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "stages": stage_telemetry,
                    "report": report,
                })
            except Exception as exc:  # noqa: BLE001 - preserve stage diagnostics
                flush_pending_claim_checkpoints()
                request.update({
                    "status": "failed",
                    "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "message": str(exc)[:1000],
                    "stages": stage_telemetry,
                })
            if claim_evidence_enabled:
                archive = archive_claim_evidence_shadow(entry, request)
                request["claim_evidence_archive_event_id"] = archive.get("event_id")
            kb.write_json(entry / "w3-shadow-report.json", request)
            ctx.info(
                "stage=w3-shadow entry_id=%s status=%s stages=%s",
                entry.name,
                request["status"],
                len(stage_telemetry),
            )
            return request

    def run_answer_revision(self, entry: Path, data: dict):
        with TraceContext() as ctx:
            ctx.info("stage=answer.revise entry_id=%s status=started", entry.name)
            routing_tier = normalize_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("answer.revise", routing_tier, raw_model_id)
            model_config = (
                model_config_for_task("answer.revise", model_id, routing_tier) if raw_model_id is not None else None
            )
            library = entry.parent.parent
            current_state = process_uploads.pipeline_state(entry)
            if current_state["state"] == "needs-source-review":
                return {"status": "blocked", "errors": ["请先确认正式题干，再提交解析修改意见"], "state": current_state}
            solution_path = entry / "solution.md"
            solution_text = solution_path.read_text(encoding="utf-8") if solution_path.exists() else ""
            if len(solution_text.strip()) < 100:
                return {
                    "status": "blocked",
                    "errors": ["后台还没有可复核解析，请先运行解析流程"],
                    "state": current_state,
                }
            reviewer = str(data.get("reviewer", "teacher")).strip()
            note = str(data.get("note", "")).strip()
            if len(note) > 4000:
                raise ValueError("单次修改意见不能超过 4000 个字符")
            if not reviewer or not note:
                return {"status": "blocked", "errors": ["请填写复核人和具体修改意见"]}
            lock = visualization_lock(entry.name)
            if not lock.acquire(blocking=False):
                return {"status": "blocked", "errors": ["此题的大模型任务正在运行，请稍后再试"]}
            try:
                requested = process_uploads.request_answer_revision(library, entry.name, reviewer, note)
                if requested.get("status") == "blocked":
                    return requested
                timestamp = kb.now_iso()
                request = {
                    "schema_version": 1,
                    "entry_id": entry.name,
                    "status": "requested",
                    "requested_at": timestamp,
                    "reviewer": reviewer,
                    "note": note,
                    "base_digest": requested.get("answer_review", {}).get("answer_digest", ""),
                    "routing_tier": routing_tier,
                    "model_id": model_id,
                    "model_display_name": model_config.get("display_name") if model_config else "",
                    "feedback_event_id": requested.get("archive", {}).get("event_id"),
                }
                request_path = entry / "answer-revision-request.json"
                kb.write_json(request_path, request)
                # Teacher feedback is the explicit batch-refresh point: make
                # the just-recorded lesson available before constructing this
                # revision's read-only evidence pack.
                with LIBRARY_INDEX_LOCK:
                    kb.rebuild_index(library)
                gateway = run_agent_gateway(
                    entry,
                    answer_revision_task(entry, note, request_path, routing_tier, model_config),
                    lambda staging, changed: validate_answer_candidate(staging, changed, entry),
                )
                if gateway["status"] == "unavailable":
                    request.update({
                        "status": "awaiting-agent",
                        "message_to_teacher": (
                            "修改意见已记录，但当前没有可用的 Agent provider；请配置 Gateway 后重试。"
                        ),
                        "gateway": gateway,
                    })
                    archive = archive_agent_result(
                        entry,
                        "answer.revise",
                        request,
                        gateway,
                        summary="Agent 按教师意见修订解析候选",
                    )
                    request["archive_event_id"] = archive.get("event_id")
                    kb.write_json(request_path, request)
                    ctx.info("stage=answer.revise entry_id=%s status=awaiting-agent", entry.name)
                    return {**request, "state": process_uploads.pipeline_state(entry)}
                succeeded = gateway["status"] == "completed"
                resulting_state = process_uploads.pipeline_state(entry)
                if succeeded:
                    request["difficulty_assessment"] = assess_entry_difficulty(entry)
                    marked = mark_answer_needs_review(library, entry, "大模型已按教师意见修订，等待教师重新复核")
                    resulting_state = marked["state"]
                    _save_agent_baseline(entry, gateway.get("changed_files", []), task_type="answer.revise")
                request.update({
                    "status": "completed" if succeeded else "failed",
                    "completed_at": kb.now_iso(),
                    "provider": gateway.get("provider"),
                    "returncode": gateway.get("returncode"),
                    "stdout": _sanitize_output(gateway.get("stdout", "")),
                    "stderr": _sanitize_output(gateway.get("stderr", "")),
                    "changed_files": gateway.get("changed_files", []),
                    "unauthorized_changes": gateway.get("unauthorized_changes", []),
                    "validation_errors": gateway.get("validation_errors", []),
                    "attempts": gateway.get("attempts", []),
                    "resulting_state": resulting_state["state"],
                    **gateway_routing_fields(gateway),
                })
                request["message_to_teacher"] = (
                    "解析和引用解释图已按意见修订，请重新复核后再批准。"
                    if succeeded
                    else gateway_failure_detail(gateway, "大模型任务执行失败，请查看错误后重试。")
                )
                archive = archive_agent_result(
                    entry,
                    "answer.revise",
                    request,
                    gateway,
                    summary="Agent 按教师意见修订解析候选",
                )
                request["archive_event_id"] = archive.get("event_id")
                kb.write_json(request_path, request)
                ctx.info(
                    "stage=answer.revise entry_id=%s status=%s provider=%s",
                    entry.name,
                    "completed" if succeeded else "failed",
                    gateway.get("provider"),
                )
                return {**request, "state": resulting_state}
            finally:
                lock.release()

    def run_visualization_chat(self, entry: Path, data: dict):
        with TraceContext() as ctx:
            ctx.info("stage=visualization.chat entry_id=%s status=started", entry.name)
            routing_tier = normalize_routing_tier(data.get("routing_tier"))
            raw_model_id = data.get("model_id")
            model_id = resolve_model_id_for_task("visualization.model", routing_tier, raw_model_id)
            model_config = (
                model_config_for_task("visualization.model", model_id, routing_tier)
                if raw_model_id is not None
                else None
            )
            library = entry.parent.parent
            message = str(data.get("message", "")).strip()
            if not message:
                raise ValueError("请描述可视化中需要修改的问题")
            if len(message) > 4000:
                raise ValueError("单次反馈不能超过 4000 个字符")
            current = process_uploads.visualization_snapshot(entry)
            current_state = process_uploads.pipeline_state(entry)
            if current_state["state"] in {"needs-source-review", "needs-analysis-and-answer", "needs-answer-review"}:
                return {"status": "blocked", "errors": ["请先批准当前解析，再调整动态可视化"], "state": current_state}
            base_digest = str(data.get("base_digest", ""))
            if base_digest and base_digest != current["artifact_digest"]:
                return {"status": "blocked", "errors": ["可视化已发生变化，请刷新后再发送反馈"]}
            lock = visualization_lock(entry.name)
            if not lock.acquire(blocking=False):
                return {"status": "blocked", "errors": ["此题的可视化任务正在运行，请稍后再试"]}
            try:
                timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
                conversation_path = entry / "visualization-conversation.json"
                conversation = read_json(
                    conversation_path, {"schema_version": 1, "entry_id": entry.name, "messages": []}
                )
                conversation.setdefault("messages", []).append({"role": "teacher", "at": timestamp, "content": message})
                request = {
                    "schema_version": 1,
                    "entry_id": entry.name,
                    "status": "requested",
                    "requested_at": timestamp,
                    "message": message,
                    "base_digest": current["artifact_digest"],
                    "routing_tier": routing_tier,
                    "model_id": model_id,
                    "model_display_name": model_config.get("display_name") if model_config else "",
                }
                previous_candidate = candidate_archive.latest_event(
                    entry, task_types={"visualization.model"}, event_type="agent-result"
                )
                feedback_event = candidate_archive.append_event(
                    library,
                    entry,
                    task_type="visualization.feedback",
                    actor="teacher",
                    event_type="feedback",
                    status="requested",
                    summary="教师提交可视化修改意见",
                    request={"message": message, "base_digest": current["artifact_digest"]},
                    feedback={"categories": teacher_feedback.feedback_categories(message, changed_files=["physics-model.json"])},
                    links={"candidate_event_id": previous_candidate.get("event_id")} if previous_candidate else {},
                )
                request["feedback_event_id"] = feedback_event["event_id"]
                # Keep query() strictly read-only while ensuring this feedback
                # and prior visual lessons are current for the next model turn.
                with LIBRARY_INDEX_LOCK:
                    kb.rebuild_index(library)
                request_path = entry / "visualization-request.json"
                kb.write_json(request_path, request)
                kb.write_json(conversation_path, conversation)
                gateway = run_agent_gateway(
                    entry,
                    visualization_task(entry, message, request_path, routing_tier, model_config),
                    validate_visualization_candidate,
                )
                if gateway["status"] == "unavailable":
                    assistant = (
                        "没有可用的 Agent provider。请求已保存在 visualization-request.json；"
                        "配置 Gateway 后可以重新提交。"
                    )
                    conversation["messages"].append({
                        "role": "assistant",
                        "at": kb.now_iso(),
                        "content": assistant,
                        "status": "awaiting-agent",
                    })
                    kb.write_json(conversation_path, conversation)
                    request.update({"status": "awaiting-agent", "message_to_teacher": assistant, "gateway": gateway})
                    archive = archive_agent_result(
                        entry,
                        "visualization.model",
                        request,
                        gateway,
                        summary="Agent 生成或修订可视化模型候选",
                    )
                    request["archive_event_id"] = archive.get("event_id")
                    kb.write_json(request_path, request)
                    return {"status": "awaiting-agent", "conversation": conversation, "visualization": current}
                build_result = None
                if gateway["status"] == "completed":
                    with LIBRARY_INDEX_LOCK:
                        build_result = process_uploads.prepare_visualization(library, entry.name, "auto")
                resulting = process_uploads.visualization_snapshot(entry)
                succeeded = (
                    gateway["status"] == "completed" and build_result is not None and build_result.get("status") == "ok"
                )
                output = gateway.get("message") or _sanitize_output(gateway.get("stdout", ""), 4000)
                if not output:
                    output = (
                        "Agent 已完成模型生成或修改，工作台已重新构建可视化。"
                        if succeeded
                        else "Agent 已退出，但未形成可通过构建的交互可视化。"
                    )
                if not succeeded:
                    output = gateway_failure_detail(gateway, output)
                status = "completed" if succeeded else "failed"
                conversation["messages"].append({
                    "role": "assistant",
                    "at": kb.now_iso(),
                    "content": output,
                    "status": status,
                    "build_status": build_result.get("status") if build_result else None,
                })
                kb.write_json(conversation_path, conversation)
                request.update({
                    "status": status,
                    "completed_at": kb.now_iso(),
                    "provider": gateway.get("provider"),
                    "returncode": gateway.get("returncode"),
                    "stderr": _sanitize_output(gateway.get("stderr", "")),
                    "unauthorized_changes": gateway.get("unauthorized_changes", []),
                    "validation_errors": gateway.get("validation_errors", []),
                    "changed_files": gateway.get("changed_files", []),
                    **gateway_routing_fields(gateway),
                    "attempts": gateway.get("attempts", []),
                    "build_status": build_result.get("status") if build_result else None,
                    "resulting_state": process_uploads.pipeline_state(entry)["state"],
                })
                archive = archive_agent_result(
                    entry,
                    "visualization.model",
                    request,
                    gateway,
                    summary="Agent 生成或修订可视化模型候选",
                )
                request["archive_event_id"] = archive.get("event_id")
                kb.write_json(request_path, request)
                return {
                    "status": status,
                    "conversation": conversation,
                    "build": build_result,
                    "visualization": resulting,
                    "state": process_uploads.pipeline_state(entry),
                }
            finally:
                lock.release()

    def clear_visualization_chat(self, entry: Path):
        lock = visualization_lock(entry.name)
        if not lock.acquire(blocking=False):
            return {"status": "blocked", "errors": ["此题的可视化任务正在运行，完成后再清空对话"]}
        try:
            conversation = {
                "schema_version": 1,
                "entry_id": entry.name,
                "messages": [],
                "cleared_at": kb.now_iso(),
            }
            kb.write_json(entry / "visualization-conversation.json", conversation)
            return {"status": "cleared", "conversation": conversation}
        finally:
            lock.release()

    def serve_static(self, path):
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = safe_child(STATIC_DIR, relative)
        self.serve_file(target, inline=True)

    def serve_file(self, path: Path, inline: bool):
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        disposition = "inline" if inline else "attachment"
        self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(path.name)}")
        self.end_headers()
        self.wfile.write(data)


def acquire_instance_lock(library: Path):
    directory = library / ".cache"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    path = directory / "teacher-console.lock"
    handle = path.open("a+", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        raise RuntimeError("已有教师工作台正在使用这个知识库；请关闭旧服务后再启动") from None
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\nstarted_at={kb.now_iso()}\n")
    handle.flush()
    return handle


def release_instance_lock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--log-file", help="可选的日志文件路径（默认仅控制台）")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("教师工作台只允许监听本机回环地址")

    configure_logging(log_file=args.log_file)
    logger.info("Starting teacher console host=%s port=%d library=%s", args.host, args.port, LIBRARY)

    try:
        instance_lock = acquire_instance_lock(LIBRARY)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    try:
        kb.init_library(LIBRARY)
        with FOLDER_LOCK:
            kb.sync_library_folders(LIBRARY)
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        logger.info("Teacher console ready at http://%s:%s", args.host, args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Shutdown requested (KeyboardInterrupt)")
        finally:
            server.server_close()
            if _JOB_MANAGER is not None:
                logger.info("Waiting for background Agent jobs to finish…")
                _JOB_MANAGER.shutdown(wait=True)
            SOURCE_CLEAN_INDEX_DEBOUNCER.flush()
    finally:
        release_instance_lock(instance_lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
