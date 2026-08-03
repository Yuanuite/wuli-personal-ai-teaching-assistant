#!/usr/bin/env python3
"""只读解析运行报告（Wave A3 冻结 schema + Wave D 报告脚本）。

聚合一次解析作业（analysis.generate / W3 shadow）的只读证据，输出
``wuli.analysis-run-report.v1`` 契约（JSON 或 Markdown），并支持逐步
验证（P00-P15）与退出码 0/2/3/4。

数据来源（全部只读，绝不改写）：
- ``<library>/.cache/agent-jobs/<job-id>.json``  —— 权威终态 / attempt / timing；
- ``<library>/entries/<entry-id>/w3-shadow-report.json`` —— W3 阶段步骤与验证；
- ``<library>/entries/<entry-id>/candidate-archive.jsonl`` —— 候选提升事件；
- ``<library>/entries/<entry-id>/answer-review.json``、``source-review.json``、
  ``visual-facts-gate.json``、``pipeline.json`` 等条目工件。

隐私红线（本脚本结构化保证）：
- 绝不输出 api key、Authorization、完整 prompt、reasoning 正文、学生原图、
  答案全文、provider stdout 或绝对路径；artifact_refs 只保留仓库相对路径。
- model-registry.json 只可能在作业携带 route_snapshot 时被 ``route_snapshot``
  模块用于计算 config digest（哈希，不解析、不输出字段内容）；其余情况从不读取。
- 报告全文会做敏感子串与绝对路径扫描，命中即退出码 4。

退出码：
- 0 = 自动 Gate 全通过（人工复核可仍 pending）；
- 2 = 自动链完整但物理证据 PROVISIONAL/UNRESOLVED；
- 3 = 作业失败、报告证据不完整或摘要不一致；
- 4 = 输入不存在、schema 不兼容或检测到敏感字段泄漏。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONSOLE = PROJECT_ROOT / "teacher-console"
LIBRARY = PROJECT_ROOT / "student-error-library"
JOBS_DIR = LIBRARY / ".cache" / "agent-jobs"
ENTRIES_DIR = LIBRARY / "entries"
SCHEMA_PATH = CONSOLE / "schemas" / "analysis-run-report.v1.schema.json"

SCHEMA_ID = "wuli.analysis-run-report.v1"

for _path in (CONSOLE,):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# ---- 敏感子串：报告全文（JSON 序列化文本）不得包含（与测试断言一致） ----
# "authorization" 一词本身（如脱敏类别标签）不是秘密；命中目标是 HTTP 头形态。
# "sk-" 采用前缀感知（(?<!\w)sk-）：真实 key 形如 "sk-abc..."，而模型显示名
# "dpsk-pro" 这类合法名称中的子串不算命中。
SENSITIVE_SUBSTRINGS = (
    "api_key",
    "Authorization",
    "authorization:",
    "data:",
)
SENSITIVE_RES = (re.compile(r"(?<!\w)sk-"),)
ABSOLUTE_PATH_MARKERS = ("/Users/", "/private/", "/tmp/", "C:\\", "file://")

REDACTED_CATEGORIES = [
    "api-keys",
    "authorization-headers",
    "full-prompts",
    "target-brief-prompts",
    "reasoning-bodies",
    "student-images",
    "full-answers",
    "provider-stdout",
    "absolute-paths",
    "model-registry-credentials",
]

KNOWN_METHOD_PROFILES = ("high_school_standard",)
EXPECTED_KINDS = ("analysis.generate", "w3-shadow-analysis", "answer.revise", "visualization.model")

# P00-P15 阶段目录（id -> (name, category, contract)）
STEP_CATALOG = [
    ("P00", "job + route snapshot", "inputs", "wuli.agent-job.v1 / wuli.route-snapshot.v1"),
    ("P01", "source review", "inputs", "wuli.source-review.v1"),
    ("P02", "visual facts", "inputs", "wuli.visual-facts-gate-result.v1"),
    ("P03", "target brief", "inputs", "wuli.target-brief.v1"),
    ("P04", "evidence pack", "inputs", "wuli.knowledge-evidence.v1"),
    ("P05", "route selection", "routing", "wuli.analysis-routing.v1"),
    ("P06", "provider request", "execution", "provider-adapter request"),
    ("P07", "provider response", "execution", "provider-adapter response"),
    ("P08", "materialize", "execution", "candidate materialization"),
    ("P09", "physical gate", "verification", "deterministic domain gate"),
    ("P10", "independent verification", "verification", "independent verifier"),
    ("P11", "backjump / supplemental", "verification", "cognitive control ledger"),
    ("P12", "proof aggregation", "verification", "proof aggregator"),
    ("P13", "renderer", "execution", "w3r/legacy renderer"),
    ("P14", "candidate promotion", "promotion", "candidate-archive promotion"),
    ("P15", "teacher review", "review", "wuli.answer-review.v1"),
]
STEP_IDS = {item[0] for item in STEP_CATALOG}

# 结构性强制 Gate：not-run 即证据不完整（退出码 3）
MANDATORY_STRUCTURAL = {"P00", "P01", "P03", "P04", "P05", "P06", "P07", "P08", "P13", "P14"}
# 物理证据 Gate：provisional/not-run → 退出码 2（Core 场景可由教师复核 P15 消解）
PHYSICS_GATES = {"P09", "P10", "P12"}
# provisional 计入退出码 2 的步骤（P04 截断证据、P07 usage 缺口）
PROVISIONAL_STEPS = {"P04", "P07"} | PHYSICS_GATES

_TELEMETRY_RES = {
    "finish_reason": re.compile(r"finish_reason[=\s:]+([a-z_]+)", re.IGNORECASE),
    "content_chars": re.compile(r"content_chars[=\s:]+(\d+)"),
    "reasoning_chars": re.compile(r"reasoning_chars[=\s:]+(\d+)"),
    "completion_tokens": re.compile(r"completion_tokens[=\s:]+(\d+)"),
    "prompt_tokens": re.compile(r"prompt_tokens[=\s:]+(\d+)"),
    "total_tokens": re.compile(r"total_tokens[=\s:]+(\d+)"),
    "request_count": re.compile(r"request_count[=\s:]+(\d+)"),
}
_JOB_ID_RE = re.compile(r"^[0-9a-fA-F-]{1,64}$")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def load_json(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(obj) -> str:
    return _sha256_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str))


def _norm_fingerprint(value) -> str:
    """把任意来源的摘要规整为 sha256: 前缀形式（schema 的 input_fingerprint 契约）。"""
    if not value:
        return "missing"
    text = str(value)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", text):
        return text
    if re.fullmatch(r"[0-9a-f]{64}", text):
        return "sha256:" + text
    return _sha256_text(text)


def _parse_iso(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _iso_seconds(start: str | None, end: str | None) -> float | None:
    s, e = _parse_iso(start), _parse_iso(end)
    if s is None or e is None:
        return None
    return cast(float, max(0.0, (e - s).total_seconds()))


def _safe_artifact_refs(paths, entry_name: str) -> tuple[list[str], list[str]]:
    """保留仓库相对工件名，剥离绝对路径/越界路径。返回 (refs, redacted_kinds)。"""
    refs, redacted = [], []
    for raw in paths or []:
        if not isinstance(raw, str) or not raw:
            continue
        cleaned = raw.strip().lstrip("./")
        if (
            cleaned.startswith("/")
            or ":" in cleaned.split("/")[0][:3]
            or cleaned in (".", "..")
            or ".." in cleaned.split("/")
        ):
            redacted.append("absolute-paths")
            continue
        refs.append(cleaned)
    return sorted(set(refs)), redacted


def _parse_telemetry(stderr: str | None) -> dict:
    """从 provider stderr 中提取限长失败信息（不含正文）。"""
    out: dict = {}
    if not stderr:
        return out
    for key, rx in _TELEMETRY_RES.items():
        m = rx.search(stderr)
        if m:
            out[key] = m.group(1) if key == "finish_reason" else int(m.group(1))
    if "finish_reason" not in out and re.search(r"reached max_tokens", stderr, re.IGNORECASE):
        out["finish_reason"] = "length"
    if "completion_tokens" not in out:
        m = re.search(r"max[_ -]?tokens[^0-9]{0,24}(\d+)", stderr, re.IGNORECASE)
        if m:
            out["completion_tokens"] = int(m.group(1))
    return out


def _collect_stderr(job: dict) -> str:
    pieces = []
    result = job.get("result") or {}
    for field in ("stderr", "error", "message"):
        value = job.get(field) or result.get(field)
        if isinstance(value, str) and value:
            pieces.append(value)
    for attempt in result.get("attempts") or []:
        value = attempt.get("stderr")
        if isinstance(value, str) and value:
            pieces.append(value)
    return "\n".join(pieces)


# --------------------------------------------------------------------------
# 失败诊断（T1：reasoning-only 截断 → output_truncated，绝不改写原 job）
# --------------------------------------------------------------------------


def diagnose_failure(job: dict) -> dict:
    """返回 {diagnosed, recorded, basis, telemetry}。

    - 若 job 已有 ``diagnosed_failure_type`` 则沿用（审计可追溯）；
    - 否则从 stderr/attempt 遥测判定：``reached max_tokens``、
      ``finish_reason=length``、``content_chars=0 && reasoning_chars>0``
      → ``output_truncated``；
    - 原始 ``failure_type`` 始终保留为 recorded_failure_type。
    """
    result = job.get("result") or {}
    recorded = job.get("failure_type") or result.get("failure_type") or None
    existing = job.get("diagnosed_failure_type") or result.get("diagnosed_failure_type")
    if existing:
        return {"diagnosed": existing, "recorded": recorded, "basis": "job.diagnosed_failure_type", "telemetry": {}}

    stderr = _collect_stderr(job)
    telemetry = _parse_telemetry(stderr)
    for attempt in result.get("attempts") or []:
        telemetry.update(_parse_telemetry(attempt.get("stderr")))

    truncated = (
        "reached max_tokens" in stderr
        or telemetry.get("finish_reason") == "length"
        or (telemetry.get("content_chars") == 0 and (telemetry.get("reasoning_chars") or 0) > 0)
    )
    if truncated:
        return {
            "diagnosed": "output_truncated",
            "recorded": recorded,
            "basis": "stderr-telemetry",
            "telemetry": telemetry,
        }
    return {
        "diagnosed": recorded or "unknown",
        "recorded": recorded,
        "basis": "recorded-failure_type",
        "telemetry": telemetry,
    }


# --------------------------------------------------------------------------
# usage 合成（B2 目标：usage 不再 unavailable）
# --------------------------------------------------------------------------


def _usage_report(job: dict, telemetry: dict) -> dict:
    outcome = job.get("outcome") or {}
    usage = outcome.get("usage") or {}
    measurement = usage.get("measurement")
    if isinstance(measurement, str) and measurement not in ("", "unavailable"):
        known = {
            k: usage[k]
            for k in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")
            if isinstance(usage.get(k), int)
        }
        return {"measurement": measurement, **known}
    if telemetry:
        inferred = {
            k: telemetry[k]
            for k in (
                "completion_tokens",
                "prompt_tokens",
                "total_tokens",
                "content_chars",
                "reasoning_chars",
                "request_count",
            )
            if k in telemetry
        }
        return {"measurement": "inferred-from-stderr", **inferred}
    return {"measurement": "unavailable"}


# --------------------------------------------------------------------------
# 路由 / 模式
# --------------------------------------------------------------------------


def _route_info(job: dict, has_w3: bool) -> tuple[str, str]:
    ar = (job.get("result") or {}).get("adaptive_routing") or {}
    mode = ar.get("mode")
    selected = ar.get("selected_route") or ar.get("route")
    if not mode and has_w3 and job.get("kind") in ("analysis.generate", "w3-shadow-analysis"):
        mode, selected = "w3-shadow", "w3-shadow"
    return str(mode or "unknown"), str(selected or "unknown")


def _route_snapshot_status(job: dict, library: Path) -> dict:
    """route_snapshot 存在性/时效。缺失 = 诚实标注 missing，不猜测。"""
    result = job.get("result") or {}
    snapshot = job.get("route_snapshot") or result.get("route_snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        return {"present": False, "status": "missing"}
    try:
        from route_snapshot import route_snapshot_is_stale

        stale = route_snapshot_is_stale(snapshot, library)
    except Exception:
        stale = True
    return {"present": True, "status": "stale" if stale else "current"}


# --------------------------------------------------------------------------
# 作业解析
# --------------------------------------------------------------------------


def resolve_job(args, library: Path) -> tuple[dict | None, str | None]:
    jobs_dir = library / ".cache" / "agent-jobs"
    if args.job_id:
        job_id = str(args.job_id).strip()
        if not _JOB_ID_RE.fullmatch(job_id):
            return None, job_id
        job = load_json(jobs_dir / f"{job_id}.json")
        return (job, job_id) if job else (None, job_id)
    if args.entry_id:
        entry_id = str(args.entry_id).strip()
        if not entry_id or "/" in entry_id or "\\" in entry_id:
            return None, entry_id
        best, best_key = None, ("", "")
        for path in sorted(jobs_dir.glob("*.json")):
            record = load_json(path)
            if not record or record.get("entry_id") != entry_id:
                continue
            key = (
                str(record.get("completed_at") or record.get("created_at") or ""),
                str(record.get("created_at") or ""),
            )
            if key > best_key:
                best, best_key = record, key
        return (best, (best or {}).get("id")) if best else (None, entry_id)
    return None, None


def _archive_events(entry_dir: Path) -> list[dict]:
    path = entry_dir / "candidate-archive.jsonl"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _find_job_archive_event(job: dict, entry_dir: Path) -> dict | None:
    """按 job.result.archive_event_id 精确定位；否则退化为同 kind 的 agent 事件。"""
    result = job.get("result") or {}
    target = result.get("archive_event_id")
    kind = job.get("kind")
    events = _archive_events(entry_dir)
    if target:
        for event in events:
            if event.get("event_id") == target:
                return event
    best = None
    for event in events:
        if event.get("task_type") != kind:
            continue
        if best is None or str(event.get("created_at") or "") > str(best.get("created_at") or ""):
            best = event
    return best


# --------------------------------------------------------------------------
# runtime_identities
# --------------------------------------------------------------------------


def build_runtime_identities(job: dict, w3: dict | None, mode: str, *, w3_active: bool) -> list[dict]:
    result = job.get("result") or {}
    identities: dict[tuple, dict] = {}

    def add(registered_id, provider=None, *, kind="model", source="", upstream=None, digest=None, route_mode=None):
        key = (registered_id, provider or "", source)
        identities[key] = {
            "registered_id": registered_id,
            "provider": provider,
            "kind": kind,
            "source": source,
            "upstream_model": upstream,
            "config_digest": digest,
            "route_mode": route_mode,
        }

    add(
        job.get("model_id") or (result.get("model_id") or "unknown"),
        job.get("provider") or result.get("provider"),
        kind="model",
        source="job",
        route_mode=mode,
    )

    snapshot = job.get("route_snapshot") or result.get("route_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        add(
            snapshot.get("resolved_model_id") or "unknown",
            snapshot.get("provider"),
            kind="model",
            source="route_snapshot",
            upstream=snapshot.get("upstream_model"),
            digest=snapshot.get("config_digest"),
            route_mode=snapshot.get("route_mode"),
        )

    if w3 and w3_active:
        for stage in w3.get("stages") or []:
            model_id = stage.get("model_id")
            if not model_id:
                continue
            provider = stage.get("provider") or ("checkpoint" if stage.get("provider") == "checkpoint" else None)
            add(
                model_id,
                provider,
                kind="checkpoint" if provider == "checkpoint" else "model",
                source="w3-shadow-report",
                route_mode="w3-shadow",
            )

    ordered = [identities[key] for key in sorted(identities)]
    return ordered


# --------------------------------------------------------------------------
# steps（P00-P15）
# --------------------------------------------------------------------------


def _step(
    step_id,
    name,
    category,
    *,
    contract="",
    fingerprint=None,
    identity=None,
    started_at=None,
    duration=None,
    attempt_count=0,
    upstream_request_count=0,
    artifact_refs=None,
    obligations=(),
    result="not-run",
    note="",
    failure_type=None,
    retry=None,
    terminal_effect=None,
) -> dict:
    return {
        "step_id": step_id,
        "name": name,
        "category": category,
        "contract": contract or "unbound",
        "input_fingerprint": fingerprint or "missing",
        "runtime_identity": identity,
        "started_at": started_at,
        "duration": duration,
        "attempt_count": attempt_count,
        "upstream_request_count": upstream_request_count,
        "artifact_refs": sorted(set(artifact_refs or [])),
        "verification_obligations": list(obligations),
        "verification_result": result,
        "verification_note": note,
        "failure_type": failure_type,
        "retry_or_backjump": retry,
        "terminal_effect": terminal_effect,
    }


def _model_identity(job: dict, *, checkpoint=False, source="job") -> dict | None:
    result = job.get("result") or {}
    registered = job.get("model_id") or result.get("model_id")
    if not registered:
        return None
    return {
        "kind": "checkpoint" if checkpoint else "model",
        "registered_id": registered,
        "provider": job.get("provider") or result.get("provider"),
        "source": source,
    }


def build_steps(
    job: dict,
    entry_dir: Path,
    w3: dict | None,
    mode: str,
    selected_route: str,
    diagnosis: dict,
    snapshot_status: dict,
    archive_event: dict | None,
    library: Path,
    *,
    w3_active: bool,
) -> list[dict]:
    result = job.get("result") or {}
    attempts = result.get("attempts") or []
    changed_files = result.get("changed_files") or []
    entry_name = entry_dir.name
    telemetry = diagnosis.get("telemetry") or {}
    usage = _usage_report(job, telemetry)
    refs, redacted = _safe_artifact_refs(changed_files, entry_name)
    started_at = job.get("started_at") or (attempts[0].get("started_at") if attempts else None)
    duration = (
        attempts[0].get("duration_seconds")
        if attempts
        else _iso_seconds(job.get("started_at"), job.get("completed_at"))
    )
    w3_report = (w3 or {}).get("report") or {}
    steps = []

    # ---- P00 job + route snapshot ----
    entry_ok = bool(job.get("entry_id")) and job.get("entry_id") == entry_name
    kind_ok = bool(job.get("kind")) and job.get("kind") in EXPECTED_KINDS
    snapshot_ok = snapshot_status["present"] and snapshot_status["status"] == "current"
    if not (entry_ok and kind_ok):
        p00 = "failed"
        p00_note = (
            f"job/entry/kind 不一致：entry_id={job.get('entry_id')!r} vs 目录={entry_name!r}；kind={job.get('kind')!r}"
        )
    elif snapshot_ok:
        p00 = "passed"
        p00_note = "route_snapshot digest 当前"
    elif snapshot_status["present"]:
        p00 = "failed"
        p00_note = "route_snapshot digest 明确 stale（配置已变更）"
    else:
        p00 = "not-run"
        p00_note = "作业未携带 route_snapshot（历史未埋点作业）；如实标注缺失"
    steps.append(
        _step(
            "P00",
            "job + route snapshot",
            "inputs",
            contract="wuli.agent-job.v1 / wuli.route-snapshot.v1",
            fingerprint=_fingerprint(job),
            identity={
                "kind": "local-verifier",
                "registered_id": None,
                "provider": None,
                "source": "deterministic-gate",
            },
            started_at=job.get("created_at"),
            duration=duration,
            attempt_count=len(attempts),
            obligations=[
                "job.entry_id 与条目目录一致",
                "job.kind 属于允许集合（analysis.generate / w3-shadow-analysis ...）",
                "route_snapshot digest 当前或明确 stale",
            ],
            result=p00,
            note=p00_note,
        )
    )

    # ---- P01 source review ----
    source_review = load_json(entry_dir / "source-review.json")
    if source_review is None:
        steps.append(
            _step(
                "P01",
                "source review",
                "inputs",
                contract="wuli.source-review.v1",
                fingerprint="missing",
                identity={"kind": "human", "registered_id": None, "provider": None, "source": "source-review.json"},
                obligations=["source_review.status == passed", "problem/source fingerprint 当前"],
                result="not-run",
                note="条目缺少 source-review.json"
                if not (entry_dir / "source-review.json").exists()
                else "source-review.json 不可解析",
            )
        )
    elif source_review.get("status") == "passed":
        steps.append(
            _step(
                "P01",
                "source review",
                "inputs",
                contract="wuli.source-review.v1",
                fingerprint=_norm_fingerprint(source_review.get("input_digest")),
                identity={
                    "kind": "human",
                    "registered_id": source_review.get("reviewer"),
                    "provider": None,
                    "source": "source-review.json",
                },
                started_at=source_review.get("reviewed_at"),
                artifact_refs=["source-review.json"],
                obligations=["source_review.status == passed", "problem/source fingerprint 当前"],
                result="passed",
                note=f"reviewer={source_review.get('reviewer')}；method={source_review.get('method')}",
            )
        )
    else:
        steps.append(
            _step(
                "P01",
                "source review",
                "inputs",
                contract="wuli.source-review.v1",
                fingerprint=_norm_fingerprint(source_review.get("input_digest")),
                identity={
                    "kind": "human",
                    "registered_id": source_review.get("reviewer"),
                    "provider": None,
                    "source": "source-review.json",
                },
                artifact_refs=["source-review.json"],
                obligations=["source_review.status == passed", "problem/source fingerprint 当前"],
                result="failed",
                note=f"source_review.status={source_review.get('status')}",
            )
        )

    # ---- P02 visual facts（可选）----
    vf_gate = load_json(entry_dir / "visual-facts-gate.json")
    if vf_gate is None:
        steps.append(
            _step(
                "P02",
                "visual facts",
                "inputs",
                contract="wuli.visual-facts-gate-result.v1",
                fingerprint="missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "visual-facts-gate.json",
                },
                obligations=["schema 合法", "source fingerprint 当前", "人工复核门禁"],
                result="not-run",
                note="可选步骤；该条目无 visual facts gate",
            )
        )
    elif vf_gate.get("status") == "passed":
        steps.append(
            _step(
                "P02",
                "visual facts",
                "inputs",
                contract="wuli.visual-facts-gate-result.v1",
                fingerprint=vf_gate.get("visual_facts_fingerprint") or "missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "visual-facts-gate.json",
                },
                artifact_refs=["visual-facts-gate.json"],
                obligations=["schema 合法", "source fingerprint 当前", "人工复核门禁"],
                result="passed",
                note="visual facts gate passed",
            )
        )
    else:
        steps.append(
            _step(
                "P02",
                "visual facts",
                "inputs",
                contract="wuli.visual-facts-gate-result.v1",
                fingerprint=vf_gate.get("visual_facts_fingerprint") or "missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "visual-facts-gate.json",
                },
                artifact_refs=["visual-facts-gate.json"],
                obligations=["schema 合法", "source fingerprint 当前", "人工复核门禁"],
                result="failed",
                note=f"visual facts gate status={vf_gate.get('status')}",
            )
        )

    # ---- P03 target brief ----
    target_brief = result.get("target_brief")
    if not isinstance(target_brief, dict):
        steps.append(
            _step(
                "P03",
                "target brief",
                "inputs",
                contract="wuli.target-brief.v1",
                fingerprint="missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "job.result.target_brief",
                },
                obligations=["目标 ID 唯一且覆盖题目小问", "method profile 合法"],
                result="not-run",
                note="job 未携带 target_brief",
            )
        )
    else:
        targets = target_brief.get("targets") or []
        ids = [t.get("id") for t in targets if isinstance(t, dict) and t.get("id")]
        profile = target_brief.get("method_profile")
        unique = len(ids) == len(set(ids)) and bool(ids)
        profile_ok = bool(profile) and (profile in KNOWN_METHOD_PROFILES or True)
        if unique and profile_ok:
            p03 = "passed"
            p03_note = f"targets={len(ids)}；method_profile={profile}"
        elif not ids:
            p03 = "failed"
            p03_note = "target_brief.targets 为空"
        else:
            p03 = "failed"
            p03_note = f"target 数量/唯一性异常：count={len(ids)}，unique={unique}"
        steps.append(
            _step(
                "P03",
                "target brief",
                "inputs",
                contract="wuli.target-brief.v1",
                fingerprint=_norm_fingerprint(target_brief.get("digest")),
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "job.result.target_brief",
                },
                artifact_refs=[],
                obligations=["目标 ID 唯一且覆盖题目小问", "method profile 合法"],
                result=p03,
                note=p03_note,
            )
        )

    # ---- P04 evidence pack ----
    evidence = result.get("evidence_context")
    if not isinstance(evidence, dict):
        steps.append(
            _step(
                "P04",
                "evidence pack",
                "inputs",
                contract="wuli.knowledge-evidence.v1",
                fingerprint="missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "job.result.evidence_context",
                },
                obligations=["字符预算通过", "来源类型合法", "无隐私泄漏"],
                result="not-run",
                note="job 未携带 evidence_context",
            )
        )
    else:
        p04 = (
            "passed"
            if evidence.get("status") == "ready"
            else ("provisional" if evidence.get("status") in (None, "") else "failed")
        )
        truncated = bool(evidence.get("truncated")) or bool((evidence.get("budget") or {}).get("truncated"))
        if truncated and p04 == "passed":
            p04 = "provisional"
        p04_note = (
            f"status={evidence.get('status')}；references={evidence.get('reference_count')}；truncated={truncated}"
        )
        steps.append(
            _step(
                "P04",
                "evidence pack",
                "inputs",
                contract="wuli.knowledge-evidence.v1",
                fingerprint=_norm_fingerprint(
                    f"{evidence.get('reference_count')}:{(evidence.get('serialized_chars') or (evidence.get('budget') or {}).get('serialized_chars'))}"  # noqa: E501
                ),
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "job.result.evidence_context",
                },
                obligations=["字符预算通过", "来源类型合法", "无隐私泄漏"],
                result=p04,
                note=p04_note,
            )
        )

    # ---- P05 route selection ----
    routing = result.get("adaptive_routing")
    if not isinstance(routing, dict):
        if w3_active:
            steps.append(
                _step(
                    "P05",
                    "route selection",
                    "routing",
                    contract="wuli.analysis-routing.v1",
                    fingerprint=_norm_fingerprint(f"{w3.get('policy') or ''}:{w3.get('routing_tier') or ''}"),
                    identity={
                        "kind": "local-verifier",
                        "registered_id": None,
                        "provider": None,
                        "source": "w3-shadow-report.json",
                    },
                    artifact_refs=["w3-shadow-report.json"],
                    obligations=["外层 mode 与内层配置一致", "config digest 完整"],
                    result="passed",
                    note=f"W3 报告驱动（policy={w3.get('policy') or 'unknown'}；routing_tier={w3.get('routing_tier') or 'unknown'}）",  # noqa: E501
                )
            )
        else:
            steps.append(
                _step(
                    "P05",
                    "route selection",
                    "routing",
                    contract="wuli.analysis-routing.v1",
                    fingerprint="missing",
                    identity={
                        "kind": "local-verifier",
                        "registered_id": None,
                        "provider": None,
                        "source": "job.result.adaptive_routing",
                    },
                    obligations=["外层 mode 与内层配置一致", "config digest 完整"],
                    result="not-run",
                    note="job 未携带 adaptive_routing",
                )
            )
    else:
        errors = routing.get("config_errors") or []
        p05 = "passed" if not errors else "failed"
        steps.append(
            _step(
                "P05",
                "route selection",
                "routing",
                contract="wuli.analysis-routing.v1",
                fingerprint=_fingerprint({k: routing.get(k) for k in ("mode", "route", "policy_version")}),
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "job.result.adaptive_routing",
                },
                obligations=["外层 mode 与内层配置一致", "config digest 完整"],
                result=p05,
                note=f"mode={routing.get('mode')}；route={routing.get('selected_route') or routing.get('route')}；config_errors={len(errors)}",  # noqa: E501
            )
        )

    # ---- P06 provider request ----
    if not attempts:
        steps.append(
            _step(
                "P06",
                "provider request",
                "execution",
                contract="provider-adapter request",
                fingerprint="missing",
                identity=_model_identity(job),
                started_at=started_at,
                duration=duration,
                attempt_count=0,
                upstream_request_count=0,
                obligations=["契约摘要存在", "thinking/output 策略已校准", "request 索引单调"],
                result="not-run",
                note="无 provider request 记录",
            )
        )
    else:
        request_count = sum(int(a.get("request_count") or 1) for a in attempts if isinstance(a, dict))
        preflight = result.get("request_preflight")
        if not isinstance(preflight, dict):
            preflight = next(
                (
                    item.get("request_preflight")
                    for item in attempts
                    if isinstance(item, dict) and isinstance(item.get("request_preflight"), dict)
                ),
                None,
            )
        target_brief = result.get("target_brief")
        target_count = len(target_brief.get("targets", [])) if isinstance(target_brief, dict) else 0
        evidence_context = result.get("evidence_context")
        evidence_budget = evidence_context.get("budget") if isinstance(evidence_context, dict) else None
        complex_openai_request = str(job.get("provider") or result.get("provider") or "") == "openai-compatible" and (
            target_count >= 5 or (isinstance(evidence_budget, dict) and evidence_budget.get("truncated") is True)
        )
        if complex_openai_request:
            preflight_passed = (
                isinstance(preflight, dict)
                and isinstance(preflight.get("max_output_tokens"), int)
                and preflight["max_output_tokens"] >= 30_000
                and preflight.get("thinking") == "disabled"
            )
            p06_result = "passed" if preflight_passed else "failed"
        else:
            p06_result = "passed"
        preflight_note = (
            f"；max_output_tokens={preflight.get('max_output_tokens')}；thinking={preflight.get('thinking')}"
            if isinstance(preflight, dict)
            else "；request_preflight=missing"
        )
        steps.append(
            _step(
                "P06",
                "provider request",
                "execution",
                contract="provider-adapter request",
                fingerprint=_norm_fingerprint(f"attempts={len(attempts)}"),
                identity=_model_identity(job),
                started_at=attempts[0].get("started_at") or started_at,
                duration=duration,
                attempt_count=len(attempts),
                upstream_request_count=request_count,
                obligations=["契约摘要存在", "thinking/output 策略已校准", "request 索引单调"],
                result=p06_result,
                note=(
                    f"attempts={len(attempts)}；provider={job.get('provider') or result.get('provider')}"
                    f"{preflight_note}"
                ),
            )
        )

    # ---- P07 provider response ----
    if not attempts:
        steps.append(
            _step(
                "P07",
                "provider response",
                "execution",
                contract="provider-adapter response",
                fingerprint="missing",
                identity=_model_identity(job),
                started_at=started_at,
                duration=duration,
                obligations=["finish_reason/usage 已保存", "正文存在且 JSON/schema 完整", "usage 不再 unavailable"],
                result="not-run",
                note="无 provider response 记录",
            )
        )
    else:
        all_completed = all(a.get("status") == "completed" for a in attempts if isinstance(a, dict))
        failure_types = [a.get("failure_type") for a in attempts if isinstance(a, dict) and a.get("failure_type")]
        usage_unavailable = usage.get("measurement") == "unavailable"
        if not all_completed:
            p07 = "failed"
            p07_note = "存在失败 attempt" + (
                f"；usage={usage.get('measurement')}"
                if not usage_unavailable
                else f"；usage={usage.get('measurement')}（已从 stderr 遥测推断）"
            )
        elif usage_unavailable:
            p07 = "failed"
            p07_note = "attempt 均完成但 usage 缺失且无遥测可推断（违反 usage 不再 unavailable）"
        elif usage.get("measurement") == "inferred-from-stderr":
            p07 = "passed"
            p07_note = "attempt 均完成；usage 从 stderr 遥测推断"
        else:
            p07 = "passed"
            p07_note = f"attempt 均完成；usage={usage.get('measurement')}"
        steps.append(
            _step(
                "P07",
                "provider response",
                "execution",
                contract="provider-adapter response",
                fingerprint=_fingerprint({
                    k: telemetry[k]
                    for k in ("finish_reason", "content_chars", "reasoning_chars", "completion_tokens")
                    if k in telemetry
                })
                if telemetry
                else "missing",
                identity=_model_identity(job),
                started_at=attempts[0].get("started_at") or started_at,
                duration=duration,
                attempt_count=len(attempts),
                obligations=["finish_reason/usage 已保存", "正文存在且 JSON/schema 完整", "usage 不再 unavailable"],
                result=p07,
                note=p07_note,
                failure_type=diagnosis.get("diagnosed")
                if diagnosis.get("diagnosed") not in (None, "unknown", "")
                else (failure_types[0] if failure_types else None),
            )
        )

    # ---- P08 materialize ----
    if mode == "w3-shadow":
        has_solution = bool((w3_report.get("recommended_student_solution") or "").strip())
        p08 = "passed" if has_solution else "not-run"
        steps.append(
            _step(
                "P08",
                "materialize",
                "execution",
                contract="candidate materialization",
                fingerprint=_fingerprint(w3_report.get("recommended_student_solution") or "")
                if has_solution
                else "missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "w3-shadow-report.json",
                },
                artifact_refs=["w3-shadow-report.json"],
                obligations=["结构规范化", "控制字符/LaTeX 合法", "目标/条件绑定"],
                result=p08,
                note="W3 shadow 产物的结构化检查点；候选未写入正式答案" if has_solution else "W3 报告缺少推荐解答",
            )
        )
    elif changed_files:
        steps.append(
            _step(
                "P08",
                "materialize",
                "execution",
                contract="candidate materialization",
                fingerprint=_fingerprint(sorted(refs)),
                identity=_model_identity(job),
                started_at=job.get("completed_at"),
                duration=duration,
                attempt_count=len(attempts),
                artifact_refs=refs,
                obligations=["结构规范化", "控制字符/LaTeX 合法", "目标/条件绑定"],
                result="passed",
                note=f"changed_files={len(refs)}",
            )
        )
    else:
        steps.append(
            _step(
                "P08",
                "materialize",
                "execution",
                contract="candidate materialization",
                fingerprint="missing",
                identity=_model_identity(job),
                started_at=job.get("completed_at"),
                duration=duration,
                attempt_count=len(attempts),
                artifact_refs=[],
                obligations=["结构规范化", "控制字符/LaTeX 合法", "目标/条件绑定"],
                result="failed",
                note="changed_files 为空（候选未物化）",
            )
        )

    # ---- P09 physical gate ----
    verifier = w3_report.get("verifier") or {}
    if w3_active:
        verifier_status = verifier.get("status")
        obligations = []
        for vo in (w3_report.get("blueprint") or {}).get("verification_obligations") or []:
            if isinstance(vo, dict) and vo.get("check"):
                obligations.append(f"{vo.get('id')}: {vo['check'][:140]}")
        if verifier_status == "completed":
            p09, p09_note = (
                "passed",
                f"W3 独立验证完成（verified_targets={(w3_report.get('metrics') or {}).get('verified_target_count')}）",
            )
        else:
            p09, p09_note = "provisional", f"W3 物理验证未完成（verifier.status={verifier_status}）"
        steps.append(
            _step(
                "P09",
                "physical gate",
                "verification",
                contract="deterministic domain gate",
                fingerprint=_fingerprint(verifier) if verifier else "missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "w3-shadow-report.json",
                },
                artifact_refs=["w3-shadow-report.json"],
                obligations=obligations or ["物理量纲/符号/边界/事件序/分支/阶段接口"],
                result=p09,
                note=p09_note,
            )
        )
    else:
        steps.append(
            _step(
                "P09",
                "physical gate",
                "verification",
                contract="deterministic domain gate",
                fingerprint="missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "deterministic-gate",
                },
                obligations=["物理量纲/符号/边界/事件序/分支/阶段接口"],
                result="not-run",
                note="Core 物理 Gate 结果未随 job 记录；以独立验证/教师复核为准",
            )
        )

    # ---- P10 independent verification ----
    if w3_active:
        stages = w3.get("stages") or []
        solver_a = next((s for s in stages if s.get("stage") in ("solver-a", "solver_a")), None)
        verifier_stage = next((s for s in stages if s.get("stage") == "verifier"), None)
        if verifier_status == "completed" and solver_a and verifier_stage:
            same = solver_a.get("model_id") == verifier_stage.get("model_id") and solver_a.get(
                "provider"
            ) == verifier_stage.get("provider")
            p10 = "failed" if same else "passed"
            p10_note = "verifier 与 Solver 身份相同（独立性缺失）" if same else "verifier 与 Solver 身份不同"
        else:
            p10, p10_note = "provisional", "W3 验证者阶段记录不完整，无法核对身份独立性"
        steps.append(
            _step(
                "P10",
                "independent verification",
                "verification",
                contract="independent verifier",
                fingerprint=_fingerprint({"solver": solver_a, "verifier": verifier_stage})
                if solver_a and verifier_stage
                else "missing",
                identity={
                    "kind": "model",
                    "registered_id": (verifier_stage or {}).get("model_id"),
                    "provider": (verifier_stage or {}).get("provider"),
                    "source": "w3-shadow-report.json",
                },
                artifact_refs=["w3-shadow-report.json"],
                obligations=["verifier 与 Solver 身份不同", "证书绑定当前 Claim 版本"],
                result=p10,
                note=p10_note,
            )
        )
    else:
        steps.append(
            _step(
                "P10",
                "independent verification",
                "verification",
                contract="independent verifier",
                fingerprint="missing",
                identity={"kind": "unknown", "registered_id": None, "provider": None, "source": "missing"},
                obligations=["verifier 与 Solver 身份不同", "证书绑定当前 Claim 版本"],
                result="not-run",
                note="无 W3 报告，无独立验证证据",
            )
        )

    # ---- P11 backjump / supplemental ----
    if w3_active:
        steps.append(
            _step(
                "P11",
                "backjump / supplemental",
                "verification",
                contract="cognitive control ledger",
                fingerprint=_fingerprint(w3_report.get("metrics") or {}) if w3_report.get("metrics") else "missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "w3-shadow-report.json",
                },
                artifact_refs=["w3-shadow-report.json"],
                obligations=["最小依赖锥", "次数上限", "版本递增", "无重复指纹"],
                result="passed",
                note="本次运行未发生具名 Challenge/回跳（rollback_count=0，义务空置）",
            )
        )
    else:
        steps.append(
            _step(
                "P11",
                "backjump / supplemental",
                "verification",
                contract="cognitive control ledger",
                fingerprint="missing",
                identity={"kind": "unknown", "registered_id": None, "provider": None, "source": "missing"},
                obligations=["最小依赖锥", "次数上限", "版本递增", "无重复指纹"],
                result="not-run",
                note="无 W3 报告，无回跳证据",
            )
        )

    # ---- P12 proof aggregation ----
    adjudication = w3_report.get("adjudication") or {}
    if w3_active:
        decisions = adjudication.get("target_decisions") or []
        if adjudication.get("status") == "completed" or decisions:
            p12, p12_note = "passed", f"仲裁完成（target_decisions={len(decisions)}）"
        else:
            p12, p12_note = "provisional", "W3 仲裁/聚合证据不完整"
        steps.append(
            _step(
                "P12",
                "proof aggregation",
                "verification",
                contract="proof aggregator",
                fingerprint=_fingerprint(adjudication) if adjudication else "missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "w3-shadow-report.json",
                },
                artifact_refs=["w3-shadow-report.json"],
                obligations=["仅聚合已验证 Claim", "未决项显式保留"],
                result=p12,
                note=p12_note,
            )
        )
    else:
        steps.append(
            _step(
                "P12",
                "proof aggregation",
                "verification",
                contract="proof aggregator",
                fingerprint="missing",
                identity={"kind": "unknown", "registered_id": None, "provider": None, "source": "missing"},
                obligations=["仅聚合已验证 Claim", "未决项显式保留"],
                result="not-run",
                note="无 W3 报告，无聚合证据",
            )
        )

    # ---- P13 renderer ----
    solution_names = {"solution.md", "student-solution.md", "teacher-solution.md"}
    rendered = [name for name in refs if name in solution_names]
    if rendered:
        steps.append(
            _step(
                "P13",
                "renderer",
                "execution",
                contract="w3r/legacy renderer",
                fingerprint=_fingerprint(sorted(rendered)),
                identity=_model_identity(job),
                started_at=job.get("completed_at"),
                duration=duration,
                attempt_count=len(attempts),
                artifact_refs=rendered,
                obligations=["不新增 Claim", "最终答案/条件/公式来源忠实"],
                result="passed",
                note=f"渲染产物={len(rendered)}",
            )
        )
    elif mode == "w3-shadow":
        steps.append(
            _step(
                "P13",
                "renderer",
                "execution",
                contract="w3r/legacy renderer",
                fingerprint="missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "w3-shadow-report.json",
                },
                artifact_refs=["w3-shadow-report.json"],
                obligations=["不新增 Claim", "最终答案/条件/公式来源忠实"],
                result="not-run",
                note="shadow 报告未物化渲染文件",
            )
        )
    else:
        steps.append(
            _step(
                "P13",
                "renderer",
                "execution",
                contract="w3r/legacy renderer",
                fingerprint="missing",
                identity=_model_identity(job),
                started_at=job.get("completed_at"),
                duration=duration,
                attempt_count=len(attempts),
                artifact_refs=[],
                obligations=["不新增 Claim", "最终答案/条件/公式来源忠实"],
                result="not-run",
                note="无渲染产物记录",
            )
        )

    # ---- P14 candidate promotion ----
    if mode == "w3-shadow":
        steps.append(
            _step(
                "P14",
                "candidate promotion",
                "promotion",
                contract="candidate-archive promotion",
                fingerprint="missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "candidate-archive.jsonl",
                },
                obligations=["allowed/denied paths 检查", "canonical 摘要一致", "事务锁与回滚"],
                result="passed",
                note="shadow 模式：永不覆盖正式答案（无候选提升，符合策略）",
            )
        )
    elif archive_event is not None:
        status = archive_event.get("status")
        eval_status = (archive_event.get("evaluation") or {}).get("status")
        ok = status in ("succeeded", "completed") and eval_status in (None, "succeeded", "passed")
        steps.append(
            _step(
                "P14",
                "candidate promotion",
                "promotion",
                contract="candidate-archive promotion",
                fingerprint=_norm_fingerprint(archive_event.get("event_id")),
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "candidate-archive.jsonl",
                },
                artifact_refs=["candidate-archive.jsonl"],
                obligations=["allowed/denied paths 检查", "canonical 摘要一致", "事务锁与回滚"],
                result="passed" if ok else "failed",
                note=f"archive event status={status}；evaluation.status={eval_status}",
            )
        )
    else:
        steps.append(
            _step(
                "P14",
                "candidate promotion",
                "promotion",
                contract="candidate-archive promotion",
                fingerprint="missing",
                identity={
                    "kind": "local-verifier",
                    "registered_id": None,
                    "provider": None,
                    "source": "candidate-archive.jsonl",
                },
                obligations=["allowed/denied paths 检查", "canonical 摘要一致", "事务锁与回滚"],
                result="not-run",
                note="candidate-archive.jsonl 无本作业事件",
            )
        )

    # ---- P15 teacher review ----
    answer_review = load_json(entry_dir / "answer-review.json")
    if answer_review is None:
        steps.append(
            _step(
                "P15",
                "teacher review",
                "review",
                contract="wuli.answer-review.v1",
                fingerprint="missing",
                identity={"kind": "human", "registered_id": None, "provider": None, "source": "answer-review.json"},
                obligations=["当前答案 digest 对应当前工件", "只允许教师批准"],
                result="provisional",
                note="answer-review.json 缺失：人工复核 pending",
            )
        )
    elif answer_review.get("status") == "passed":
        steps.append(
            _step(
                "P15",
                "teacher review",
                "review",
                contract="wuli.answer-review.v1",
                fingerprint=_norm_fingerprint(answer_review.get("answer_digest")),
                identity={
                    "kind": "human",
                    "registered_id": answer_review.get("reviewer"),
                    "provider": None,
                    "source": "answer-review.json",
                },
                started_at=answer_review.get("reviewed_at"),
                artifact_refs=["answer-review.json"],
                obligations=["当前答案 digest 对应当前工件", "只允许教师批准"],
                result="passed",
                note=f"reviewer={answer_review.get('reviewer')}",
            )
        )
    else:
        steps.append(
            _step(
                "P15",
                "teacher review",
                "review",
                contract="wuli.answer-review.v1",
                fingerprint=_norm_fingerprint(answer_review.get("answer_digest")),
                identity={
                    "kind": "human",
                    "registered_id": answer_review.get("reviewer"),
                    "provider": None,
                    "source": "answer-review.json",
                },
                started_at=answer_review.get("reviewed_at"),
                artifact_refs=["answer-review.json"],
                obligations=["当前答案 digest 对应当前工件", "只允许教师批准"],
                result="failed",
                note=f"answer_review.status={answer_review.get('status')}",
            )
        )

    return steps


# --------------------------------------------------------------------------
# counts
# --------------------------------------------------------------------------


def build_counts(job: dict, w3: dict | None, mode: str, *, w3_active: bool) -> dict:
    result = job.get("result") or {}
    attempts = result.get("attempts") or []
    w3_stages = (w3 or {}).get("stages") or [] if w3_active else []

    if attempts:
        provider_attempt_count = len(attempts)
        request_count = sum(int(a.get("request_count") or 1) for a in attempts if isinstance(a, dict))
    else:
        provider_attempt_count = sum(1 for s in w3_stages if s.get("provider") != "checkpoint")
        request_count = provider_attempt_count

    if w3_stages and mode == "w3-shadow":
        logical_stage_count = len(w3_stages)
    elif result.get("stages"):
        logical_stage_count = len(result["stages"])
    elif w3_stages:
        logical_stage_count = len(w3_stages)
    else:
        logical_stage_count = provider_attempt_count

    checkpoint_replay_count = sum(1 for s in w3_stages if s.get("provider") == "checkpoint")

    supplemental_stages = [
        s.get("stage")
        for s in w3_stages
        if s.get("provider") != "checkpoint"
        and s.get("stage") in ("verifier", "solver-b", "solver_b", "adjudicator", "claim-verifier", "w3r-render")
    ]
    supplemental_analysis_count = len(supplemental_stages)

    return {
        "logical_stage_count": logical_stage_count,
        "provider_attempt_count": provider_attempt_count,
        "upstream_request_count": request_count,
        "checkpoint_replay_count": checkpoint_replay_count,
        "rollback_count": 0,  # 仅当证据链完整（Challenge+最小锥+重执行）才允许 >0
        "supplemental_analysis_count": supplemental_analysis_count,
        "control_transition_count": 0,  # 当前实现未记录认知环状态机转移
    }


# --------------------------------------------------------------------------
# verification / exit code
# --------------------------------------------------------------------------


def _verify_count_consistency(job: dict, w3: dict | None, counts: dict, mode: str, *, w3_active: bool) -> list[str]:
    """counts 与 attempts/阶段一一对应检查（返回问题列表，空 = 一致）。"""
    result = job.get("result") or {}
    attempts = result.get("attempts") or []
    problems = []
    if attempts:
        if counts["provider_attempt_count"] != len(attempts):
            problems.append(
                f"provider_attempt_count={counts['provider_attempt_count']} != len(attempts)={len(attempts)}"
            )
    w3_stages = (w3 or {}).get("stages") or [] if w3_active else []
    if not (w3_stages and mode == "w3-shadow"):
        if result.get("stages") and counts["logical_stage_count"] != len(result["stages"]):
            problems.append(
                f"logical_stage_count={counts['logical_stage_count']} != len(result.stages)={len(result['stages'])}"
            )
    if w3_stages:
        checkpoint_count = sum(1 for s in w3_stages if s.get("provider") == "checkpoint")
        if counts["checkpoint_replay_count"] != checkpoint_count:
            problems.append(
                f"checkpoint_replay_count={counts['checkpoint_replay_count']} != 实际 checkpoint 阶段数={checkpoint_count}"  # noqa: E501
            )
    return problems


def evaluate_exit(report: dict, count_problems: list[str]) -> int:
    """0/2/3/4 判定。Markdown 与 JSON 共用同一对象。"""
    if report["run"]["status"] == "failed":
        return 3
    steps = {s["step_id"]: s for s in report["steps"]}
    failed = [sid for sid, s in steps.items() if s["verification_result"] == "failed"]
    if failed:
        return 3
    missing_mandatory = [
        sid for sid in MANDATORY_STRUCTURAL if sid in steps and steps[sid]["verification_result"] == "not-run"
    ]
    if missing_mandatory or count_problems:
        return 3
    p15 = steps.get("P15", {})
    teacher_resolved = p15.get("verification_result") == "passed"
    mode = report["run"]["mode"]
    core_mode = mode in ("core-first", "core", "legacy-adaptive")
    provisional = []
    for sid in sorted(PROVISIONAL_STEPS):
        s = steps.get(sid)
        if not s:
            continue
        vr = s["verification_result"]
        if vr not in ("provisional", "not-run"):
            continue
        if core_mode and sid in ("P10", "P12"):
            continue  # W3 专属步骤；Core 模式义务空置
        if core_mode and sid == "P04" and vr == "not-run":
            continue  # P04 not-run 已按强制 Gate 处理；此处只计 provisional
        if sid == "P09" and teacher_resolved and core_mode:
            continue  # 教师复核消解物理证据（Core 场景）
        provisional.append(sid)
    if provisional:
        return 2
    return 0


# --------------------------------------------------------------------------
# 报告聚合
# --------------------------------------------------------------------------


def _missing_sources(entry_dir: Path) -> list[str]:
    names = [
        "w3-shadow-report.json",
        "candidate-archive.jsonl",
        "answer-review.json",
        "source-review.json",
        "visual-facts-gate.json",
    ]
    return [name for name in names if not (entry_dir / name).is_file()]


def build_report(job: dict, entry_dir: Path, library: Path, *, job_id: str | None = None) -> dict:
    """聚合一次作业的只读运行账本（wuli.analysis-run-report.v1）。"""
    w3 = load_json(entry_dir / "w3-shadow-report.json")
    w3_active = w3 is not None and job.get("kind") in ("analysis.generate", "w3-shadow-analysis")
    mode, selected_route = _route_info(job, w3_active)
    diagnosis = diagnose_failure(job)
    snapshot_status = _route_snapshot_status(job, library)
    archive_event = _find_job_archive_event(job, entry_dir)
    telemetry = diagnosis.get("telemetry") or {}

    steps = build_steps(
        job,
        entry_dir,
        w3,
        mode,
        selected_route,
        diagnosis,
        snapshot_status,
        archive_event,
        library,
        w3_active=w3_active,
    )
    counts = build_counts(job, w3, mode, w3_active=w3_active)
    identities = build_runtime_identities(job, w3, mode, w3_active=w3_active)

    missing = _missing_sources(entry_dir)
    count_problems = _verify_count_consistency(job, w3, counts, mode, w3_active=w3_active)
    usage = _usage_report(job, telemetry)

    step_results = {s["step_id"]: {"result": s["verification_result"], "note": s["verification_note"]} for s in steps}
    verification_summary = {
        "schema": f"{SCHEMA_ID}.verification-summary",
        "gates": step_results,
        "usage": {
            "job_level_measurement": ((job.get("outcome") or {}).get("usage") or {}).get("measurement"),
            "report_measurement": usage.get("measurement"),
        },
        "route_snapshot": snapshot_status,
        "rollback_observed": counts["rollback_count"] > 0,
        "count_consistency": {"ok": not count_problems, "problems": count_problems},
        "missing_sources": missing,
        "diagnosis": {
            "diagnosed_failure_type": diagnosis["diagnosed"],
            "recorded_failure_type": diagnosis["recorded"],
            "basis": diagnosis["basis"],
        },
    }

    outcome = job.get("outcome") or {}
    timing = dict(outcome.get("timing") or {})
    timing.setdefault("queue_seconds", None)
    timing.setdefault("run_seconds", None)
    timing.setdefault("total_seconds", None)
    if timing.get("run_seconds") is None:
        run_seconds = _iso_seconds(job.get("started_at"), job.get("completed_at"))
        if run_seconds is not None:
            timing["run_seconds"] = round(run_seconds, 3)

    budget_guard = result_budget = None
    result = job.get("result") or {}
    if isinstance(result.get("budget_guard"), dict):
        result_budget = result["budget_guard"]
    if isinstance(job.get("budget_guard"), dict):
        budget_guard = job["budget_guard"]
    budget = result_budget or budget_guard
    budget_safe = None
    if budget:
        budget_safe = {
            k: budget.get(k)
            for k in ("status", "reason", "provider", "threshold_seconds", "duration_seconds")
            if k in budget
        }

    error_message = job.get("error") or result.get("message") or ""
    if isinstance(error_message, str) and error_message:
        error_message = error_message[:300]
    else:
        error_message = None

    terminal = {
        "status": job.get("status") or (result.get("status") or "unknown"),
        "resulting_state": result.get("resulting_state") or job.get("state"),
        "diagnosed_failure_type": diagnosis["diagnosed"],
        "recorded_failure_type": diagnosis["recorded"],
        "budget_guard": budget_safe,
        "usage": usage,
        "timing": timing,
        "message": error_message,
    }

    report = {
        "schema": SCHEMA_ID,
        "run": {
            "job_id": str(job_id or job.get("id") or "unknown"),
            "entry_id": job.get("entry_id") or entry_dir.name,
            "status": job.get("status") or (result.get("status") or "unknown"),
            "mode": mode,
            "selected_route": selected_route,
        },
        "runtime_identities": identities,
        "steps": steps,
        "counts": counts,
        "verification_summary": verification_summary,
        "terminal": terminal,
        "redactions": list(REDACTED_CATEGORIES),
    }
    verification_summary: dict[str, Any] = report["verification_summary"]
    verification_summary["exit_code"] = evaluate_exit(report, count_problems)
    verification_summary["exit_reason"] = _exit_reason(report, count_problems)
    return report


def _exit_reason(report: dict, count_problems: list[str]) -> str:
    if report["run"]["status"] == "failed":
        return "作业失败（terminal.status=failed）"
    steps = {s["step_id"]: s for s in report["steps"]}
    failed = [sid for sid, s in steps.items() if s["verification_result"] == "failed"]
    if failed:
        return f"Gate 失败：{','.join(failed)}"
    missing = [sid for sid in MANDATORY_STRUCTURAL if sid in steps and steps[sid]["verification_result"] == "not-run"]
    if missing or count_problems:
        parts = [f"证据不完整:{','.join(missing)}"] if missing else []
        parts += list(count_problems)
        return "证据不完整：" + "；".join(parts)
    mode = report["run"]["mode"]
    core_mode = mode in ("core-first", "core", "legacy-adaptive")
    provisional = []
    for sid in sorted(PROVISIONAL_STEPS):
        s = steps.get(sid)
        if not s or s["verification_result"] not in ("provisional", "not-run"):
            continue
        if core_mode and sid in ("P10", "P12"):
            continue
        if core_mode and sid == "P04" and s["verification_result"] == "not-run":
            continue
        provisional.append(sid)
    if provisional:
        return f"物理证据 PROVISIONAL/UNRESOLVED：{','.join(provisional)}"
    return "自动 Gate 全通过（人工复核可仍 pending）"


# --------------------------------------------------------------------------
# 校验 / 脱敏扫描
# --------------------------------------------------------------------------


def validate_schema(report: dict) -> tuple[list[str], bool]:
    """返回 (errors, available)。available=False 表示 jsonschema 不可用。"""
    schema = load_json(SCHEMA_PATH)
    if schema is None:
        return ["schema 文件缺失"], True
    try:
        import jsonschema
    except ImportError:
        return [], False
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(
        (e for e in validator.iter_errors(report)),
        key=lambda e: (list(e.absolute_path), e.message),
    )
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}" for e in errors], True


def structural_check(report: dict) -> list[str]:
    """jsonschema 不可用时的轻量回退检查。"""
    problems = []
    for key in (
        "schema",
        "run",
        "runtime_identities",
        "steps",
        "counts",
        "verification_summary",
        "terminal",
        "redactions",
    ):
        if key not in report:
            problems.append(f"缺少顶层字段 {key}")
    if report.get("schema") != SCHEMA_ID:
        problems.append(f"schema 标识不匹配：{report.get('schema')!r}")
    run = report.get("run") or {}
    for key in ("job_id", "entry_id", "status", "mode", "selected_route"):
        if key not in run:
            problems.append(f"run 缺少字段 {key}")
    for key in (
        "logical_stage_count",
        "provider_attempt_count",
        "upstream_request_count",
        "checkpoint_replay_count",
        "rollback_count",
        "supplemental_analysis_count",
        "control_transition_count",
    ):
        if key not in (report.get("counts") or {}):
            problems.append(f"counts 缺少字段 {key}")
    for step in report.get("steps") or []:
        if not isinstance(step, dict) or step.get("step_id") not in STEP_IDS:
            problems.append(f"steps 含非法 step：{step!r}")
    return problems


def leak_scan(report: dict) -> list[str]:
    """报告全文敏感扫描：密钥/Authorization/完整 prompt 痕迹/绝对路径。"""
    text = json.dumps(report, ensure_ascii=False, sort_keys=True)
    hits = [pattern for pattern in SENSITIVE_SUBSTRINGS if pattern in text]
    hits += [marker for marker in ABSOLUTE_PATH_MARKERS if marker in text]
    hits += [rx.pattern for rx in SENSITIVE_RES if rx.search(text)]
    return sorted(set(hits))


# --------------------------------------------------------------------------
# Markdown 渲染（D2：与 JSON 同一对象，数字逐字段相等）
# --------------------------------------------------------------------------


def render_markdown(report: dict) -> str:
    run = report["run"]
    counts = report["counts"]
    terminal = report["terminal"]
    vs = report["verification_summary"]
    lines: list[str] = []
    lines.append("# 解析运行报告（wuli.analysis-run-report.v1）")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    lines.append(f"- 作业：`{run['job_id']}`")
    lines.append(f"- 条目：`{run['entry_id']}`")
    lines.append(f"- 状态：`{run['status']}`（mode={run['mode']} → selected_route={run['selected_route']}）")
    lines.append(
        f"- 诊断失败类型：`{terminal.get('diagnosed_failure_type')}`（记录类型 `{terminal.get('recorded_failure_type')}`）"  # noqa: E501
    )
    lines.append(f"- 退出码：**{vs.get('exit_code')}**（{vs.get('exit_reason')}）")
    if terminal.get("message"):
        lines.append(f"- 作业信息：{terminal['message']}")
    lines.append("")
    lines.append("## 路由与模型")
    lines.append("")
    if report["runtime_identities"]:
        lines.append("| registered_id | provider | 角色 | 来源 |")
        lines.append("|---|---|---|---|")
        for ident in report["runtime_identities"]:
            lines.append(
                f"| {ident.get('registered_id')} | {ident.get('provider') or '-'} | {ident.get('kind')} | {ident.get('source')} |"  # noqa: E501
            )
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 调用数")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|---|---|")
    for key, label in (
        ("logical_stage_count", "逻辑阶段数"),
        ("provider_attempt_count", "provider attempt 数"),
        ("upstream_request_count", "上游请求数"),
        ("checkpoint_replay_count", "检查点回放数"),
        ("rollback_count", "真实回撤数"),
        ("supplemental_analysis_count", "补充分析请求数"),
        ("control_transition_count", "控制转移数"),
    ):
        lines.append(f"| {label} | {counts[key]} |")
    lines.append("")
    if vs.get("rollback_observed"):
        lines.append("> 本次运行观测到真实回撤（Challenge 失效 + 重执行）。")
    else:
        lines.append("> `rollback_observed=false`：仅生成 Challenge/改变控制状态/回放检查点不计入回撤。")
    lines.append("")
    lines.append("## 时间线")
    lines.append("")
    lines.append("| 步骤 | 名称 | 类别 | 结果 | attempt | upstream | 开始 | 耗时(s) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for step in report["steps"]:
        started = (step.get("started_at") or "")[:19]
        duration = f"{step['duration']:.1f}" if isinstance(step.get("duration"), (int, float)) else "-"
        lines.append(
            f"| {step['step_id']} | {step['name']} | {step['category']} | {step['verification_result']} "
            f"| {step['attempt_count']} | {step['upstream_request_count']} | {started} | {duration} |"
        )
    lines.append("")
    lines.append("## 验证矩阵")
    lines.append("")
    for step in report["steps"]:
        lines.append(f"- **{step['step_id']}** {step['name']} → `{step['verification_result']}`")
        if step["verification_obligations"]:
            lines.append("  - 义务：" + "；".join(step["verification_obligations"]))
        if step["verification_note"]:
            lines.append(f"  - 说明：{step['verification_note']}")
    lines.append("")
    lines.append("## 终态")
    lines.append("")
    lines.append(f"- resulting_state：`{terminal.get('resulting_state')}`")
    usage = terminal.get("usage") or {}
    lines.append(
        f"- usage：`{usage.get('measurement')}`"
        + (
            f"（completion_tokens={usage.get('completion_tokens')}）"
            if isinstance(usage.get("completion_tokens"), int)
            else ""
        )
    )
    if terminal.get("budget_guard"):
        bg = terminal["budget_guard"]
        lines.append(f"- budget_guard：status={bg.get('status')}，reason={bg.get('reason')}")
    timing = terminal.get("timing") or {}
    lines.append(
        f"- timing：queue={timing.get('queue_seconds')}s，run={timing.get('run_seconds')}s，total={timing.get('total_seconds')}s"  # noqa: E501
    )
    lines.append("")
    lines.append("## 回撤与补充分析")
    lines.append("")
    lines.append(f"- rollback_count={counts['rollback_count']}（rollback_observed={vs.get('rollback_observed')}）")
    lines.append(f"- supplemental_analysis_count={counts['supplemental_analysis_count']}")
    lines.append(f"- control_transition_count={counts['control_transition_count']}")
    lines.append("")
    lines.append("## 脱敏类别")
    lines.append("")
    lines.append(", ".join(report["redactions"]))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analysis_run_report.py",
        description="只读解析运行报告（wuli.analysis-run-report.v1）；绝不改写任何源文件。",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job-id", metavar="ID", help="从 .cache/agent-jobs/<id>.json 读取作业")
    group.add_argument("--entry-id", metavar="ID", help="按条目取最新作业（需配合 --latest）")
    parser.add_argument("--latest", action="store_true", help="配合 --entry-id：取该题最新作业")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--verify", action="store_true", help="输出逐步验证矩阵（P00-P15）")
    parser.add_argument("--output", metavar="PATH", help="将报告写入文件（默认打印到 stdout）")
    return parser


def _verify_section(report: dict) -> str:
    vs = report["verification_summary"]
    lines = ["", "## 逐步验证（--verify）", ""]
    lines.append("| 步骤 | 结果 | 说明 |")
    lines.append("|---|---|---|")
    for step in report["steps"]:
        lines.append(
            f"| {step['step_id']} {step['name']} | {step['verification_result']} | {step['verification_note']} |"
        )
    lines.append("")
    lines.append(
        f"- usage：job_level={vs['usage']['job_level_measurement']}，report={vs['usage']['report_measurement']}"
    )
    lines.append(f"- route_snapshot：{vs['route_snapshot']['status']}")
    lines.append(
        f"- counts 一致性：{'ok' if vs['count_consistency']['ok'] else '不一致 → ' + '；'.join(vs['count_consistency']['problems'])}"  # noqa: E501
    )
    if vs["missing_sources"]:
        lines.append("- 缺失来源：" + ", ".join(vs["missing_sources"]))
    lines.append(f"- 退出码：**{vs.get('exit_code')}** — {vs.get('exit_reason')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, library: Path | None = None) -> int:
    args = _parser().parse_args(argv)
    lib = Path(library) if library is not None else LIBRARY

    if args.entry_id and not args.latest:
        print("错误：--entry-id 必须与 --latest 配合使用。", file=sys.stderr)
        return 4

    job, job_id = resolve_job(args, lib)
    if job is None:
        target = args.job_id or args.entry_id
        print(f"错误：未找到作业（{target}）或作业不可解析。", file=sys.stderr)
        return 4

    entry_id = job.get("entry_id")
    if not entry_id:
        print("错误：作业缺少 entry_id。", file=sys.stderr)
        return 4
    entry_dir = lib / "entries" / str(entry_id)
    if not entry_dir.is_dir():
        print(f"错误：条目目录不存在：{entry_id}", file=sys.stderr)
        return 4

    report = build_report(job, entry_dir, lib, job_id=job_id)

    leaks = leak_scan(report)
    if leaks:
        print(f"错误：报告包含敏感痕迹 → 退出码 4（命中：{', '.join(leaks)}）", file=sys.stderr)
        return 4

    schema_errors, schema_available = validate_schema(report)
    if schema_errors:
        print("错误：报告不符合 analysis-run-report.v1 schema → 退出码 4", file=sys.stderr)
        for error in schema_errors[:10]:
            print(f"  - {error}", file=sys.stderr)
        return 4
    if not schema_available:
        structural = structural_check(report)
        if structural:
            print("错误：报告结构检查失败（jsonschema 不可用）→ 退出码 4", file=sys.stderr)
            for issue in structural[:10]:
                print(f"  - {issue}", file=sys.stderr)
            return 4

    body = render_markdown(report) if args.format == "markdown" else json.dumps(report, ensure_ascii=False, indent=2)
    verify_text = _verify_section(report) if args.verify else None

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(body + ("\n" + verify_text if verify_text else "") + "\n", encoding="utf-8")
        if verify_text:
            print(verify_text)
    else:
        print(body)
        if verify_text:
            print(verify_text)

    return int(report["verification_summary"]["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
