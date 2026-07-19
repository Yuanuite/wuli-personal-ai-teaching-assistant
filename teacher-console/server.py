#!/usr/bin/env python3
"""Local-only teacher console for the student error-library lifecycle."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import tempfile
import threading
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
sys.path.insert(0, str(SKILL_SCRIPTS))
sys.path.insert(0, str(CONSOLE_DIR))

import kb  # noqa: E402
import process_uploads  # noqa: E402
import public_site  # noqa: E402

from agent_gateway import AgentGateway  # noqa: E402
from agent_jobs import AgentJobManager  # noqa: E402


MAX_UPLOAD = 30 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
ALLOWED_UPLOADS = kb.SUPPORTED_EXTENSIONS
FOLDER_LOCK = threading.RLock()
LIBRARY_INDEX_LOCK = threading.RLock()
VISUALIZATION_LOCKS: dict[str, threading.RLock] = {}
VISUALIZATION_LOCKS_GUARD = threading.Lock()
PUBLICATION_LOCK = threading.RLock()
AGENT_GATEWAY = AgentGateway()
_JOB_MANAGER: AgentJobManager | None = None
_JOB_MANAGER_LOCK = threading.Lock()
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
PROTECTED_RECORD_FIELDS = {
    "schema_version", "id", "kind", "status", "answer_status", "created_at", "updated_at",
    "library_folder", "source", "ocr", "source_review", "answer_review", "visualization_review",
    "generated_from", "review",
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
            priority = {name: index for index, name in enumerate(("adapter", "openai-compatible", "legacy-command", "codex", "claude"))}
            available.sort(key=lambda item: priority.get(item.get("name"), 99))
            result["selected"] = available[0]["name"] if available else None
        elif not any(item.get("name") == result.get("selected") for item in available):
            result["selected"] = None
        result["available"] = result.get("selected") is not None
        if not result["available"]:
            result["reason"] = "没有通过项目隐私门禁的 provider"
    return result


def agent_available() -> bool:
    return bool(agent_health()["available"])


ROUTING_TIERS = {"auto", "economy", "expert"}


def normalize_routing_tier(value) -> str:
    tier = str(value or "auto").strip().lower()
    if tier not in ROUTING_TIERS:
        raise ValueError("routing_tier must be auto, economy, or expert")
    return tier


def gateway_routing_fields(gateway: dict) -> dict:
    keys = ("routing_tier", "requested_tier", "model_tier", "model", "usage", "routing_notice")
    return {key: gateway[key] for key in keys if key in gateway}


def queue_agent_job(kind: str, entry: Path, callback, *, routing_tier: str = "auto") -> dict:
    def guarded_callback():
        with visualization_lock(entry.name):
            return callback()

    return job_manager().submit(
        kind, entry.name, guarded_callback,
        metadata={"routing_tier": normalize_routing_tier(routing_tier)},
    )


def visualization_lock(entry_id: str) -> threading.RLock:
    with VISUALIZATION_LOCKS_GUARD:
        return VISUALIZATION_LOCKS.setdefault(entry_id, threading.RLock())


def _remote_agent_allowed(entry: Path) -> bool:
    config = kb.load_json(entry.parent.parent / "config.json", {})
    return config.get("privacy", {}).get("allow_remote_agent") is True


def remote_agent_allowed() -> bool:
    config = kb.load_json(LIBRARY / "config.json", {})
    return config.get("privacy", {}).get("allow_remote_agent") is True


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
) -> dict:
    routing_tier = normalize_routing_tier(routing_tier)
    context_files = {
        ".agent-context/project-rules.md": str(PROJECT_ROOT / "CLAUDE.md"),
        ".agent-context/responsibility-matrix.md": str(PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "references" / "responsibility-matrix.md"),
    }
    if kind in {"analysis.generate", "answer.revise"}:
        context_files[".agent-context/answer-template.md"] = str(PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "references" / "answer-template.md")
    if kind == "analysis.generate" and routing_tier != "economy":
        context_files[".agent-context/secondary-conclusions.json"] = str(PROJECT_ROOT / ".claude" / "skills" / "build-physics-simulator" / "references" / "secondary-conclusions.json")
    if routing_tier == "expert":
        context_files[".agent-context/library-skill.md"] = str(PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "SKILL.md")
    if kind == "visualization.model":
        context_files.update({
            ".agent-context/simulator-skill.md": str(PROJECT_ROOT / ".claude" / "skills" / "build-physics-simulator" / "SKILL.md"),
            ".agent-context/physics-model.schema.json": str(PROJECT_ROOT / ".claude" / "skills" / "build-physics-simulator" / "references" / "physics-model.schema.json"),
        })
    scoped_prompt = (
        "先读取当前候选目录 .agent-context/ 中的项目规则、Skill 与职责边界；这些文件只读且不得作为输出。"
        + prompt
    )
    return {
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
        "workspace_root": str(Path(tempfile.gettempdir()) / "wuli-agent-workspaces"),
        "context_files": context_files,
    }


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


def analysis_task(entry: Path, instruction: str, routing_tier: str = "auto") -> dict:
    prompt = (
        f"处理错题知识库条目 {entry.resolve()}。{instruction}。"
        "必须遵循候选区提供的项目规则、答案模板与职责边界；只处理这个条目。"
        "来源题干已由教师复核后方可进入本步骤。检索已有方法，独立解题，更新 record.json，"
        "生成 student-solution.md、teacher-solution.md、同步的 solution.md 和至少一张本地解释图；"
        "标准解析阶段默认不要创建 physics-model.json 或交互仿真；教师在可视化页明确提出生成要求后，"
        "再由专门入口调用 build-physics-simulator。当前目录是 Gateway 隔离候选区，只做内容校验，不要重建 canonical 知识索引。"
        "不要修改 pipeline 或复核记录，不要替教师执行 approve-answer，也不要 finish 或交付；"
        "Gateway 提升候选后会统一重建索引并让条目进入 needs-answer-review。"
    )
    return _agent_task(
        entry,
        "analysis.generate",
        prompt,
        ["record.json", "student-solution.md", "teacher-solution.md", "solution.md", "assets/**"],
        input_paths=["problem.md", "record.json", "student-solution.md", "teacher-solution.md", "solution.md", *sorted(answer_asset_names(entry))],
        denied_paths=sorted(source_asset_names(entry)),
        routing_tier=routing_tier,
    )


def visualization_task(entry: Path, message: str, request_path: Path, routing_tier: str = "auto") -> dict:
    has_model = (entry / "physics-model.json").exists()
    task = (
        "当前尚无 physics-model.json。教师已经明确请求生成可交互可视化；请调用 build-physics-simulator Skill，"
        "从已复核题干和答案独立建立完整物理模型，写入 physics-model.json，并执行模型校验。"
        if not has_model else
        "当前已有 physics-model.json。请调用 build-physics-simulator Skill，修正物理阶段、事件、轨迹、控件或文字，并执行模型校验。"
    )
    prompt = (
        f"你正在为错题条目 {entry.resolve()} 生成或修复教学可视化。教师要求：{message}\n"
        "必须遵循候选区提供的项目规则、职责边界与 build-physics-simulator Skill。"
        "只允许修改当前条目的 physics-model.json，以及当前条目 assets/ 中由可视化专用的素材；"
        "不要直接手改生成的 physics-simulator.html/zip，不要修改题干与答案 Markdown，不要改其他条目或全局 Skill。"
        "先读取已复核题干、答案、现有模型（如有）和构建记录。"
        f"{task}"
        "若当前 builder 确实不支持所需过程，必须明确说明，不能套用错误模板或假装已生成。"
        "静态解释 SVG 属于解析复核，不在这里修改。"
        "执行模型校验即可，确定性 HTML 构建将由教师工作台在你退出后完成。"
        "严禁替教师调用 approve-visualization、approve-answer 或 finish。"
        f"本轮请求的审计记录位于 {request_path.resolve()}。"
    )
    answer_assets = answer_asset_names(entry)
    return _agent_task(
        entry,
        "visualization.model",
        prompt,
        ["physics-model.json", "assets/visualization-*", "assets/simulation-*"],
        input_paths=[
            "problem.md", "record.json", "student-solution.md", "teacher-solution.md", "solution.md",
            "physics-model.json", request_path.name, "assets/visualization-*", "assets/simulation-*",
            "visualization/simulation-build.json",
        ],
        denied_paths=sorted(source_asset_names(entry) | answer_assets),
        request_path=request_path,
        requires_change=not has_model,
        routing_tier=routing_tier,
    )


def answer_revision_task(entry: Path, note: str, request_path: Path, routing_tier: str = "auto") -> dict:
    routing_tier = normalize_routing_tier(routing_tier)
    model_instruction = (
        "若条目已有 physics-model.json 且教师意见涉及共同物理语义，可同步修正模型；"
        if routing_tier != "economy" and (entry / "physics-model.json").exists()
        else "本档位不读取或修改 physics-model.json；若意见确实涉及共同物理语义，请返回说明并由教师新建深度任务；"
    )
    prompt = (
        f"你正在根据教师意见修订错题条目 {entry.resolve()} 的解析。教师意见：{note}\n"
        "必须遵循候选区提供的项目规则、答案模板与职责边界，并采用高中生应知的低认知负担解法。"
        "只处理当前条目：核对已批准题干，修改 student-solution.md、teacher-solution.md，并让 solution.md 与教师版同步；"
        "可修改或新增当前条目 assets/ 中被答案 Markdown 引用的解释 SVG/PNG。不要修改题目原图、problem.md、"
        "source-review.json、record.json、pipeline.json、任何复核批准文件、其他条目或全局 Skill。"
        f"{model_instruction}不要仅为一张静态解释图新建模型，"
        "不要直接修改 visualization/ 中生成的 HTML/ZIP。完成后校验分层答案和本地图片引用。"
        "严禁替教师调用 approve-answer、approve-visualization 或 finish；修订后必须等待教师再次复核。"
        f"本轮请求记录位于 {request_path.resolve()}。"
    )
    allowed = ["solution.md", "student-solution.md", "teacher-solution.md", "assets/**"]
    if routing_tier != "economy" and (entry / "physics-model.json").exists():
        allowed.append("physics-model.json")
    inputs = [
        "problem.md", "student-solution.md", "teacher-solution.md", "solution.md",
        request_path.name, *sorted(answer_asset_names(entry)),
    ]
    if routing_tier != "economy" and (entry / "physics-model.json").exists():
        inputs.append("physics-model.json")
    return _agent_task(
        entry,
        "answer.revise",
        prompt,
        allowed,
        input_paths=inputs,
        denied_paths=sorted(source_asset_names(entry)),
        request_path=request_path,
        routing_tier=routing_tier,
    )


def entry_file_digests(entry: Path) -> dict[str, str]:
    """Snapshot current-entry files for a post-Agent scope audit."""
    return {
        str(path.relative_to(entry)): kb.sha256_file(path)
        for path in sorted(entry.rglob("*"))
        if path.is_file()
    }


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


def validate_answer_candidate(staging: Path, _changed: list[str], canonical_entry: Path | None = None) -> list[str]:
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
    if canonical_entry is not None:
        baseline = kb.load_json(canonical_entry / "record.json", {})
        candidate = kb.load_json(staging / "record.json", {})
        for field in sorted(PROTECTED_RECORD_FIELDS):
            if candidate.get(field) != baseline.get(field):
                errors.append(f"record.json protected field changed: {field}")
    errors.extend(kb.validate_entry(LIBRARY, staging, ready_rules=True, require_answer_review=False))
    return sorted(set(errors))


def validate_visualization_candidate(staging: Path, _changed: list[str]) -> list[str]:
    if not (staging / "physics-model.json").is_file():
        return ["physics-model.json is missing"]
    with tempfile.TemporaryDirectory(prefix=".agent-model-check-") as output_name:
        report = process_uploads.build_simulator(staging, Path(output_name), "skip")
    if report.get("status") == "ok":
        return []
    return report.get("errors") or [f"visualization model validation failed: {report.get('status', 'unknown')}"]


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
    return {
        "status": "saved",
        "layer": layer,
        "answer_digest": marked["review"]["answer_digest"],
        "state": marked["state"],
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
            f"/api/visualization/{quote(entry.name)}/physics-simulator.html"
            f"?v={visualization['artifact_digest'][:12]}"
        )
    visualization.pop("html", None)
    publication = public_site.publication_snapshot(entry, PUBLIC_SITE)
    publication_images = public_site.public_image_snapshot(entry)
    latest_job = job_manager().latest_for_entry(entry.name)
    for source in publication_images.get("sources", []):
        source["url"] = f"/api/entry-file/{quote(entry.name)}/{quote(source['relative'])}"
    if publication["preview_ready"]:
        publication["preview_url"] = f"/api/public-preview/{quote(entry.name)}/viewer.html?id={quote(publication['public_id'])}"
    if publication["published_local"]:
        publication["local_site_url"] = f"/api/public-site/viewer.html?id={quote(publication['public_id'])}"
    summary.update({
        "record": record,
        "problem": (entry / "problem.md").read_text(encoding="utf-8") if (entry / "problem.md").exists() else "",
        "student_solution": (entry / "student-solution.md").read_text(encoding="utf-8") if (entry / "student-solution.md").exists() else "",
        "teacher_solution": (entry / "teacher-solution.md").read_text(encoding="utf-8") if (entry / "teacher-solution.md").exists() else ((entry / "solution.md").read_text(encoding="utf-8") if (entry / "solution.md").exists() else ""),
        "source_review_record": read_json(entry / "source-review.json", {}),
        "answer_review_record": read_json(entry / "answer-review.json", record.get("answer_review", {})),
        "analysis_request": read_json(entry / "analysis-request.json", {}),
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
                return self.json_response({"status": "ok", "project": str(PROJECT_ROOT), "agent_configured": agent["available"], "agent": agent})
            if path == "/api/agent/providers":
                return self.json_response(agent_health())
            if path == "/api/jobs":
                entry_id = parse_qs(parsed.query).get("entry_id", [""])[0]
                if not entry_id:
                    raise ValueError("entry_id is required")
                record = job_manager().latest_for_entry(unquote(entry_id))
                return self.json_response({"job": job_manager().public(record) if record else None})
            if path.startswith("/api/jobs/"):
                job_id = unquote(path.removeprefix("/api/jobs/")).strip("/")
                return self.json_response(job_manager().public(job_manager().get(job_id)))
            if path == "/api/entries":
                kb.init_library(LIBRARY)
                with FOLDER_LOCK:
                    groups = kb.sync_library_folders(LIBRARY)
                entries = [entry_summary(entry) for entry in reversed(list(kb.entry_dirs(LIBRARY)))]
                by_id = {entry["id"]: entry for entry in entries}
                folders = [
                    {"name": group["name"], "entries": [by_id[entry_id] for entry_id in group["entries"] if entry_id in by_id]}
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
                return self.json_response(AGENT_GATEWAY.probe(
                    str(data.get("provider", "")),
                    timeout_seconds=int(data.get("timeout_seconds", 120)),
                    allow_remote=remote_agent_allowed(),
                ))
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
                "auto",
                str(data.get("vision_capability", "unavailable")),
                None,
                None,
            )
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
        if action == "approve-source":
            problem = str(data.get("problem", ""))
            if len(problem.strip()) < 30:
                raise ValueError("正式题干过短")
            kb.write_text(entry / "problem.md", problem)
            with LIBRARY_INDEX_LOCK:
                result = process_uploads.approve_source(LIBRARY, entry.name, reviewer, note)
        elif action == "analyze":
            tier = normalize_routing_tier(data.get("routing_tier"))
            result = queue_agent_job("analysis.generate", entry, lambda: self.run_analysis(entry, data), routing_tier=tier)
        elif action == "save-answer":
            if process_uploads.pipeline_state(entry)["state"] == "needs-source-review":
                result = {"status": "blocked", "errors": ["请先确认正式题干，再编辑解析"]}
            else:
                result = self.save_answer(entry, data)
        elif action == "approve-answer":
            with LIBRARY_INDEX_LOCK:
                result = process_uploads.approve_answer(LIBRARY, entry.name, reviewer, note)
        elif action == "request-revision":
            tier = normalize_routing_tier(data.get("routing_tier"))
            result = queue_agent_job("answer.revise", entry, lambda: self.run_answer_revision(entry, data), routing_tier=tier)
        elif action == "build-visualization":
            current_state = process_uploads.pipeline_state(entry)
            if current_state["state"] in {"needs-source-review", "needs-analysis-and-answer", "needs-answer-review"}:
                result = {"status": "blocked", "errors": ["请先生成并批准解析，再构建动态可视化"], "state": current_state}
            elif not (entry / "physics-model.json").exists():
                request = {
                    "message": str(data.get("message", "")).strip() or "我想为这道题生成一个可交互的可视化结果。",
                    "base_digest": str(data.get("base_digest", "")),
                }
                request["routing_tier"] = normalize_routing_tier(data.get("routing_tier"))
                result = queue_agent_job("visualization.model", entry, lambda: self.run_visualization_chat(entry, request), routing_tier=request["routing_tier"])
            else:
                with visualization_lock(entry.name):
                    result = process_uploads.prepare_visualization(LIBRARY, entry.name, str(data.get("runtime_check", "auto")))
        elif action == "approve-visualization":
            current_state = process_uploads.pipeline_state(entry)
            if current_state["state"] in {"needs-source-review", "needs-analysis-and-answer", "needs-answer-review"}:
                result = {"status": "blocked", "errors": ["请先批准当前解析，再批准动态可视化"], "state": current_state}
            else:
                result = process_uploads.approve_visualization(LIBRARY, entry.name, reviewer, note)
        elif action == "visualization-chat":
            tier = normalize_routing_tier(data.get("routing_tier"))
            result = queue_agent_job("visualization.model", entry, lambda: self.run_visualization_chat(entry, data), routing_tier=tier)
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
        status = HTTPStatus.CONFLICT if result.get("status") == "blocked" else (HTTPStatus.ACCEPTED if result.get("status") == "queued" else HTTPStatus.OK)
        self.json_response(result, status)

    def save_answer(self, entry: Path, data: dict):
        return save_answer_entry(LIBRARY, entry, data)

    def run_analysis(self, entry: Path, data: dict):
        routing_tier = normalize_routing_tier(data.get("routing_tier"))
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
        }
        kb.write_json(entry / "analysis-request.json", request)
        gateway = AGENT_GATEWAY.run(
            analysis_task(entry, instruction, routing_tier),
            lambda staging, changed: validate_answer_candidate(staging, changed, entry),
        )
        if gateway["status"] == "unavailable":
            request["status"] = "awaiting-agent"
            request["message"] = "没有可用的 Agent provider；请求已保留，可在配置 Gateway 后重试。"
            request["gateway"] = gateway
            kb.write_json(entry / "analysis-request.json", request)
            return request
        succeeded = gateway["status"] == "completed"
        if succeeded:
            marked = mark_answer_needs_review(LIBRARY, entry, "Agent 已生成分层解析，等待教师复核")
            resulting_state = marked["state"]
        else:
            resulting_state = process_uploads.pipeline_state(entry)
        request.update({
            "status": "completed" if succeeded else "failed",
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "provider": gateway.get("provider"),
            "returncode": gateway.get("returncode"),
            "stdout": gateway.get("stdout", ""),
            "stderr": gateway.get("stderr", ""),
            "changed_files": gateway.get("changed_files", []),
            "unauthorized_changes": gateway.get("unauthorized_changes", []),
            "validation_errors": gateway.get("validation_errors", []),
            "attempts": gateway.get("attempts", []),
            "resulting_state": resulting_state["state"],
            **gateway_routing_fields(gateway),
        })
        if not succeeded:
            request["message"] = gateway.get("message", "Agent 未形成可复核答案")
        kb.write_json(entry / "analysis-request.json", request)
        pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry.name})
        pipeline["state"] = resulting_state["state"]
        pipeline["analysis_request"] = request
        kb.write_json(entry / "pipeline.json", pipeline)
        return request

    def run_answer_revision(self, entry: Path, data: dict):
        routing_tier = normalize_routing_tier(data.get("routing_tier"))
        library = entry.parent.parent
        current_state = process_uploads.pipeline_state(entry)
        if current_state["state"] == "needs-source-review":
            return {"status": "blocked", "errors": ["请先确认正式题干，再提交解析修改意见"], "state": current_state}
        solution_path = entry / "solution.md"
        solution_text = solution_path.read_text(encoding="utf-8") if solution_path.exists() else ""
        if len(solution_text.strip()) < 100:
            return {"status": "blocked", "errors": ["后台还没有可复核解析，请先运行解析流程"], "state": current_state}
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
            }
            request_path = entry / "answer-revision-request.json"
            kb.write_json(request_path, request)
            gateway = AGENT_GATEWAY.run(
                answer_revision_task(entry, note, request_path, routing_tier),
                lambda staging, changed: validate_answer_candidate(staging, changed, entry),
            )
            if gateway["status"] == "unavailable":
                request.update({
                    "status": "awaiting-agent",
                    "message_to_teacher": "修改意见已记录，但当前没有可用的 Agent provider；请配置 Gateway 后重试。",
                    "gateway": gateway,
                })
                kb.write_json(request_path, request)
                return {**request, "state": process_uploads.pipeline_state(entry)}
            succeeded = gateway["status"] == "completed"
            resulting_state = process_uploads.pipeline_state(entry)
            if succeeded:
                marked = mark_answer_needs_review(library, entry, "大模型已按教师意见修订，等待教师重新复核")
                resulting_state = marked["state"]
            request.update({
                "status": "completed" if succeeded else "failed",
                "completed_at": kb.now_iso(),
                "provider": gateway.get("provider"),
                "returncode": gateway.get("returncode"),
                "stdout": gateway.get("stdout", ""),
                "stderr": gateway.get("stderr", ""),
                "changed_files": gateway.get("changed_files", []),
                "unauthorized_changes": gateway.get("unauthorized_changes", []),
                "validation_errors": gateway.get("validation_errors", []),
                "attempts": gateway.get("attempts", []),
                "resulting_state": resulting_state["state"],
                **gateway_routing_fields(gateway),
            })
            request["message_to_teacher"] = (
                "解析和引用解释图已按意见修订，请重新复核后再批准。"
                if succeeded else gateway.get("message", "大模型任务执行失败，请查看错误后重试。")
            )
            kb.write_json(request_path, request)
            return {**request, "state": resulting_state}
        finally:
            lock.release()

    def run_visualization_chat(self, entry: Path, data: dict):
        routing_tier = normalize_routing_tier(data.get("routing_tier"))
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
            conversation = read_json(conversation_path, {"schema_version": 1, "entry_id": entry.name, "messages": []})
            conversation.setdefault("messages", []).append({"role": "teacher", "at": timestamp, "content": message})
            request = {
                "schema_version": 1,
                "entry_id": entry.name,
                "status": "requested",
                "requested_at": timestamp,
                "message": message,
                "base_digest": current["artifact_digest"],
                "routing_tier": routing_tier,
            }
            request_path = entry / "visualization-request.json"
            kb.write_json(request_path, request)
            kb.write_json(conversation_path, conversation)
            gateway = AGENT_GATEWAY.run(visualization_task(entry, message, request_path, routing_tier), validate_visualization_candidate)
            if gateway["status"] == "unavailable":
                assistant = "没有可用的 Agent provider。请求已保存在 visualization-request.json；配置 Gateway 后可以重新提交。"
                conversation["messages"].append({"role": "assistant", "at": kb.now_iso(), "content": assistant, "status": "awaiting-agent"})
                kb.write_json(conversation_path, conversation)
                request.update({"status": "awaiting-agent", "message_to_teacher": assistant, "gateway": gateway})
                kb.write_json(request_path, request)
                return {"status": "awaiting-agent", "conversation": conversation, "visualization": current}
            build_result = None
            if gateway["status"] == "completed":
                with LIBRARY_INDEX_LOCK:
                    build_result = process_uploads.prepare_visualization(library, entry.name, "auto")
            resulting = process_uploads.visualization_snapshot(entry)
            succeeded = (
                gateway["status"] == "completed"
                and build_result is not None
                and build_result.get("status") == "ok"
            )
            output = gateway.get("message") or gateway.get("stdout", "").strip()[-4000:]
            if not output:
                output = "Agent 已完成模型生成或修改，工作台已重新构建可视化。" if succeeded else "Agent 已退出，但未形成可通过构建的交互可视化。"
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
                "stderr": gateway.get("stderr", ""),
                "unauthorized_changes": gateway.get("unauthorized_changes", []),
                "validation_errors": gateway.get("validation_errors", []),
                "changed_files": gateway.get("changed_files", []),
                **gateway_routing_fields(gateway),
                "attempts": gateway.get("attempts", []),
                "build_status": build_result.get("status") if build_result else None,
                "resulting_state": process_uploads.pipeline_state(entry)["state"],
            })
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
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("教师工作台只允许监听本机回环地址")
    try:
        instance_lock = acquire_instance_lock(LIBRARY)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    try:
        kb.init_library(LIBRARY)
        with FOLDER_LOCK:
            kb.sync_library_folders(LIBRARY)
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        print(f"教师工作台已启动：http://{args.host}:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            if _JOB_MANAGER is not None:
                print("正在等待后台 Agent 安全结束…", flush=True)
                _JOB_MANAGER.shutdown(wait=True)
    finally:
        release_instance_lock(instance_lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
