#!/usr/bin/env python3
"""Local-only teacher console for the student error-library lifecycle."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shlex
import shutil
import subprocess
import sys
import threading
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

import kb  # noqa: E402
import process_uploads  # noqa: E402
import public_site  # noqa: E402


MAX_UPLOAD = 30 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024
ALLOWED_UPLOADS = kb.SUPPORTED_EXTENSIONS
FOLDER_LOCK = threading.RLock()
VISUALIZATION_LOCKS: dict[str, threading.Lock] = {}
VISUALIZATION_LOCKS_GUARD = threading.Lock()
PUBLICATION_LOCK = threading.RLock()
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


def agent_available() -> bool:
    return bool(
        os.environ.get("TEACHER_CONSOLE_AGENT_COMMAND", "").strip()
        or shutil.which("codex")
        or shutil.which("claude")
    )


def visualization_lock(entry_id: str) -> threading.Lock:
    with VISUALIZATION_LOCKS_GUARD:
        return VISUALIZATION_LOCKS.setdefault(entry_id, threading.Lock())


def agent_command(entry: Path, prompt: str, request_path: Path | None = None, working_dir: Path | None = None) -> list[str] | None:
    configured = os.environ.get("TEACHER_CONSOLE_AGENT_COMMAND", "").strip()
    if configured:
        replacements = {
            "{entry}": str(entry.resolve()),
            "{entry_id}": entry.name,
            "{prompt}": prompt,
            "{request}": str(request_path.resolve()) if request_path else "",
        }
        tokens = shlex.split(configured)
        for source, target in replacements.items():
            tokens = [token.replace(source, target) for token in tokens]
        return tokens
    codex = shutil.which("codex")
    if codex:
        return [
            codex,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "-C",
            str((working_dir or PROJECT_ROOT).resolve()),
            prompt,
        ]
    claude = shutil.which("claude")
    if claude:
        return [claude, "--print", "--permission-mode", "acceptEdits", "--no-session-persistence", prompt]
    return None


def analysis_command(entry: Path, instruction: str) -> list[str] | None:
    prompt = (
        f"处理错题知识库条目 {entry.resolve()}。{instruction}。"
        "必须遵循项目 CLAUDE.md 和 manage-student-error-library Skill；只处理这个条目。"
        "来源题干已由教师复核后方可进入本步骤。检索已有方法，独立解题，更新 record.json，"
        "生成 student-solution.md、teacher-solution.md、同步的 solution.md 和至少一张本地解释图；"
        "标准解析阶段默认不要创建 physics-model.json 或交互仿真；教师在可视化页明确提出生成要求后，"
        "再由专门入口调用 build-physics-simulator。执行适当校验和 kb.py rebuild。"
        "不要替教师执行 approve-answer，也不要 finish 或交付；完成后必须让条目进入 needs-answer-review。"
    )
    return agent_command(entry, prompt)


def visualization_command(entry: Path, message: str, request_path: Path) -> list[str] | None:
    has_model = (entry / "physics-model.json").exists()
    task = (
        "当前尚无 physics-model.json。教师已经明确请求生成可交互可视化；请调用 build-physics-simulator Skill，"
        "从已复核题干和答案独立建立完整物理模型，写入 physics-model.json，并执行模型校验。"
        if not has_model else
        "当前已有 physics-model.json。请调用 build-physics-simulator Skill，修正物理阶段、事件、轨迹、控件或文字，并执行模型校验。"
    )
    prompt = (
        f"你正在为错题条目 {entry.resolve()} 生成或修复教学可视化。教师要求：{message}\n"
        "必须遵循项目 CLAUDE.md、manage-student-error-library 与 build-physics-simulator Skill。"
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
    return agent_command(entry, prompt, request_path, entry)


def answer_revision_command(entry: Path, note: str, request_path: Path) -> list[str] | None:
    prompt = (
        f"你正在根据教师意见修订错题条目 {entry.resolve()} 的解析。教师意见：{note}\n"
        "必须遵循项目 CLAUDE.md 与 manage-student-error-library Skill，并采用高中生应知的低认知负担解法。"
        "只处理当前条目：核对已批准题干，修改 student-solution.md、teacher-solution.md，并让 solution.md 与教师版同步；"
        "可修改或新增当前条目 assets/ 中被答案 Markdown 引用的解释 SVG/PNG。不要修改题目原图、problem.md、"
        "source-review.json、record.json、pipeline.json、任何复核批准文件、其他条目或全局 Skill。"
        "若条目已有 physics-model.json 且教师意见涉及共同物理语义，可同步修正模型；不要仅为一张静态解释图新建模型，"
        "不要直接修改 visualization/ 中生成的 HTML/ZIP。完成后校验分层答案和本地图片引用。"
        "严禁替教师调用 approve-answer、approve-visualization 或 finish；修订后必须等待教师再次复核。"
        f"本轮请求记录位于 {request_path.resolve()}。"
    )
    return agent_command(entry, prompt, request_path, entry)


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
    return {str(Path(name)) for name in record.get("source", {}).get("stored_files", [])}


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
                return self.json_response({"status": "ok", "project": str(PROJECT_ROOT), "agent_configured": agent_available()})
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
                return self.serve_file(safe_child(output, relative), inline=False)
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
        reviewer = str(data.get("reviewer", "teacher"))
        note = str(data.get("note", ""))
        if action == "approve-source":
            problem = str(data.get("problem", ""))
            if len(problem.strip()) < 30:
                raise ValueError("正式题干过短")
            kb.write_text(entry / "problem.md", problem)
            result = process_uploads.approve_source(LIBRARY, entry.name, reviewer, note)
        elif action == "analyze":
            result = self.run_analysis(entry, data)
        elif action == "save-answer":
            if process_uploads.pipeline_state(entry)["state"] == "needs-source-review":
                result = {"status": "blocked", "errors": ["请先确认正式题干，再编辑解析"]}
            else:
                result = self.save_answer(entry, data)
        elif action == "approve-answer":
            result = process_uploads.approve_answer(LIBRARY, entry.name, reviewer, note)
        elif action == "request-revision":
            result = self.run_answer_revision(entry, data)
        elif action == "build-visualization":
            current_state = process_uploads.pipeline_state(entry)
            if current_state["state"] in {"needs-source-review", "needs-analysis-and-answer", "needs-answer-review"}:
                result = {"status": "blocked", "errors": ["请先生成并批准解析，再构建动态可视化"], "state": current_state}
            elif not (entry / "physics-model.json").exists():
                result = self.run_visualization_chat(entry, {
                    "message": str(data.get("message", "")).strip() or "我想为这道题生成一个可交互的可视化结果。",
                    "base_digest": str(data.get("base_digest", "")),
                })
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
            result = self.run_visualization_chat(entry, data)
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
            result = process_uploads.finish(LIBRARY, entry.name, None, str(data.get("simulator", "auto")))
        else:
            raise ValueError(f"unknown entry action: {action}")
        status = HTTPStatus.CONFLICT if result.get("status") == "blocked" else HTTPStatus.OK
        self.json_response(result, status)

    def save_answer(self, entry: Path, data: dict):
        return save_answer_entry(LIBRARY, entry, data)

    def run_analysis(self, entry: Path, data: dict):
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
        }
        kb.write_json(entry / "analysis-request.json", request)
        command = analysis_command(entry, instruction)
        if not command:
            request["status"] = "awaiting-agent"
            request["message"] = "未配置页面可调用的 Agent；请在 Claude Code/Codex 中说：处理这个待分析条目。"
            kb.write_json(entry / "analysis-request.json", request)
            return request
        completed = subprocess.run(command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False, timeout=1800)
        resulting_state = process_uploads.pipeline_state(entry)
        succeeded = completed.returncode == 0 and resulting_state["state"] in {"needs-answer-review", "ready-to-finish", "delivered"}
        request.update({
            "status": "completed" if succeeded else "failed",
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "resulting_state": resulting_state["state"],
        })
        if completed.returncode == 0 and not succeeded:
            request["message"] = "Agent 已退出，但条目尚未形成可复核答案；请查看输出后重试或手动处理。"
        kb.write_json(entry / "analysis-request.json", request)
        pipeline = kb.load_json(entry / "pipeline.json", {"schema_version": 1, "entry_id": entry.name})
        pipeline["state"] = resulting_state["state"]
        pipeline["analysis_request"] = request
        kb.write_json(entry / "pipeline.json", pipeline)
        return request

    def run_answer_revision(self, entry: Path, data: dict):
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
            }
            request_path = entry / "answer-revision-request.json"
            kb.write_json(request_path, request)
            command = answer_revision_command(entry, note, request_path)
            if not command:
                request.update({
                    "status": "awaiting-agent",
                    "message_to_teacher": "修改意见已记录，但当前未配置页面可调用的 Agent；请在 Claude Code/Codex 中处理 answer-revision-request.json。",
                })
                kb.write_json(request_path, request)
                return {**request, "state": process_uploads.pipeline_state(entry)}

            before = entry_file_digests(entry)
            existing_model = (entry / "physics-model.json").exists()
            source_assets = source_asset_names(entry)
            completed = subprocess.run(command, cwd=entry, text=True, capture_output=True, check=False, timeout=1800)
            after = entry_file_digests(entry)
            changed = changed_entry_files(before, after)

            def answer_change_allowed(relative: str) -> bool:
                if relative in {"solution.md", "student-solution.md", "teacher-solution.md"}:
                    return True
                if relative == "physics-model.json":
                    return existing_model
                path = Path(relative)
                return (
                    path.parts[:1] == ("assets",)
                    and relative not in source_assets
                    and path.suffix.lower() in kb.IMAGE_EXTENSIONS | {".svg"}
                )

            unauthorized = [name for name in changed if not answer_change_allowed(name)]
            validation_errors: list[str] = []
            if completed.returncode == 0 and not unauthorized:
                teacher_layer = entry / "teacher-solution.md"
                if teacher_layer.exists():
                    kb.write_text(entry / "solution.md", teacher_layer.read_text(encoding="utf-8"))
                validation_errors = kb.validate_entry(library, entry, ready_rules=True, require_answer_review=False)
            succeeded = completed.returncode == 0 and not unauthorized and not validation_errors
            resulting_state = process_uploads.pipeline_state(entry)
            if succeeded:
                marked = mark_answer_needs_review(library, entry, "大模型已按教师意见修订，等待教师重新复核")
                resulting_state = marked["state"]
            request.update({
                "status": "completed" if succeeded else "failed",
                "completed_at": kb.now_iso(),
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-2000:],
                "changed_files": changed,
                "unauthorized_changes": unauthorized,
                "validation_errors": validation_errors,
                "resulting_state": resulting_state["state"],
            })
            if unauthorized:
                request["message_to_teacher"] = f"检测到超出当前解析范围的修改：{', '.join(unauthorized)}；本轮未通过范围审计。"
            elif validation_errors:
                request["message_to_teacher"] = "修订后的解析未通过校验：" + "；".join(validation_errors)
            elif completed.returncode != 0:
                request["message_to_teacher"] = "大模型任务执行失败，请查看错误后重试。"
            else:
                request["message_to_teacher"] = "解析和引用解释图已按意见修订，请重新复核后再批准。"
            kb.write_json(request_path, request)
            return {**request, "state": resulting_state}
        except subprocess.TimeoutExpired:
            request = read_json(entry / "answer-revision-request.json", {})
            request.update({
                "status": "failed",
                "completed_at": kb.now_iso(),
                "message_to_teacher": "大模型运行超过 30 分钟，已停止本轮任务。",
            })
            kb.write_json(entry / "answer-revision-request.json", request)
            return {"status": "blocked", "errors": ["解析修订 Agent 运行超时"], "request": request}
        finally:
            lock.release()

    def run_visualization_chat(self, entry: Path, data: dict):
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
            }
            request_path = entry / "visualization-request.json"
            kb.write_json(request_path, request)
            kb.write_json(conversation_path, conversation)
            command = visualization_command(entry, message, request_path)
            if not command:
                assistant = "未配置页面可调用的 Agent。请求已保存在 visualization-request.json；请在 Claude Code/Codex 中处理后，再回到页面点击“构建 / 刷新预览”。"
                conversation["messages"].append({"role": "assistant", "at": kb.now_iso(), "content": assistant, "status": "awaiting-agent"})
                kb.write_json(conversation_path, conversation)
                request.update({"status": "awaiting-agent", "message_to_teacher": assistant})
                kb.write_json(request_path, request)
                return {"status": "awaiting-agent", "conversation": conversation, "visualization": current}

            before = entry_file_digests(entry)
            source_assets = source_asset_names(entry)
            answer_assets: set[str] = set()
            for markdown_name in ("solution.md", "student-solution.md", "teacher-solution.md"):
                markdown_path = entry / markdown_name
                if not markdown_path.exists():
                    continue
                for raw in kb.markdown_image_refs(markdown_path.read_text(encoding="utf-8")):
                    relative = raw.strip().strip("<>").split(maxsplit=1)[0]
                    if not relative.startswith(("http:", "https:", "data:")):
                        answer_assets.add(str(Path(relative)))
            completed = subprocess.run(command, cwd=entry, text=True, capture_output=True, check=False, timeout=1800)
            changed = changed_entry_files(before, entry_file_digests(entry))

            def visualization_change_allowed(relative: str) -> bool:
                path = Path(relative)
                return relative == "physics-model.json" or (
                    path.parts[:1] == ("assets",)
                    and relative not in source_assets
                    and relative not in answer_assets
                )

            unauthorized = [name for name in changed if not visualization_change_allowed(name)]
            build_result = None
            if completed.returncode == 0 and not unauthorized:
                build_result = process_uploads.prepare_visualization(library, entry.name, "auto")
            resulting = process_uploads.visualization_snapshot(entry)
            succeeded = (
                completed.returncode == 0
                and not unauthorized
                and build_result is not None
                and build_result.get("status") == "ok"
            )
            output = completed.stdout.strip()[-4000:]
            if unauthorized:
                output = f"检测到 Agent 修改了不允许的文件：{', '.join(unauthorized)}。本轮不能进入可视化批准。\n{output}"
            elif not output:
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
                "returncode": completed.returncode,
                "stderr": completed.stderr[-2000:],
                "unauthorized_changes": unauthorized,
                "changed_files": changed,
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
        except subprocess.TimeoutExpired:
            conversation = read_json(entry / "visualization-conversation.json", {"messages": []})
            conversation.setdefault("messages", []).append({
                "role": "assistant",
                "at": kb.now_iso(),
                "content": "Agent 运行超过 30 分钟，已停止本轮任务。请缩小修改范围后重试。",
                "status": "failed",
            })
            kb.write_json(entry / "visualization-conversation.json", conversation)
            return {"status": "blocked", "errors": ["可视化 Agent 运行超时"], "conversation": conversation}
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and os.environ.get("TEACHER_CONSOLE_ALLOW_NETWORK") != "true":
        raise SystemExit("non-loopback binding requires TEACHER_CONSOLE_ALLOW_NETWORK=true")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
