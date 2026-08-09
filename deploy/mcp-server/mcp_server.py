#!/usr/bin/env python3
"""悟理 (Wuli) MCP server — 只读检索与知识库浏览接口。

薄包装（thin wrapper）：工具直接调用项目既有检索层（knowledge_store / kb），
不重写业务逻辑。只读设计：永不重建索引、永不写条目、永不触碰批准与发布状态。

传输方式（二选一）：
  --stdio（默认）  : 本地 MCP 客户端（Claude Desktop / Qwen Code 等）
  --http           : Streamable HTTP，挂载在 /mcp，用于远程/公网部署
                     （赛事投稿要求该形态，且监听 0.0.0.0:$PORT）

隐私边界：
  - 公网部署默认只允许访问显式指定的库（WULI_LIBRARY / --library）；未指定时
    启动失败并给出指引，绝不静默暴露真实错题库。
  - get_entry 默认不返回教师版/学生版解析全文；retrieve 的匹配片段默认只含
    标签与题干类文档，除非显式 include_solution_snippets=true。
  - 所有工具返回结构化 dict；业务异常转为 {"error", "msg", "trace_id"}，
    不向 Agent 端抛 traceback。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_SCRIPTS = PROJECT_ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
if str(LIBRARY_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(LIBRARY_SCRIPTS))
TEACHER_CONSOLE = PROJECT_ROOT / "teacher-console"
if str(TEACHER_CONSOLE) not in sys.path:
    sys.path.insert(0, str(TEACHER_CONSOLE))

import kb  # noqa: E402
import knowledge_store  # noqa: E402

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 mcp SDK：pip install \"mcp[cli]\"") from exc

logger = logging.getLogger("wuli.mcp")

DEFAULT_LIBRARY = PROJECT_ROOT / "student-error-library"
DEFAULT_DEMO_LIBRARY = Path(__file__).resolve().parent / "demo-library"
# 公网安全的匹配片段来源：只暴露标签与题干，不暴露解析正文
PUBLIC_DOCUMENT_KINDS = {"metadata", "problem", "source_review"}

_STATE: dict[str, Path | None] = {"library": None}


def _current_library() -> Path:
    library = _STATE["library"]
    if library is None:
        raise RuntimeError("library not configured")
    return library


def _error(code: str, msg: str, **extra: Any) -> dict[str, Any]:
    return {"error": code, "msg": str(msg), "trace_id": uuid.uuid4().hex[:12], **extra}


def _guard(result: dict[str, Any], unavailable_reason: str = "knowledge-store-unavailable") -> dict[str, Any]:
    """把业务层的 unavailable 状态统一成结构化错误，其余原样返回。"""
    if isinstance(result, dict) and result.get("status") == "unavailable":
        return _error(
            result.get("reason", unavailable_reason),
            result.get("reason") or "知识库不可用",
        )
    return result


# ---------------------------------------------------------------------------
# 业务函数（同步、薄包装）
# ---------------------------------------------------------------------------

def _list_entries(folder: str, status: str, limit: int) -> list[dict[str, Any]]:
    library = _current_library()
    entries: list[dict[str, Any]] = []
    for entry in kb.entry_dirs(library):
        record = kb.load_json(entry / "record.json", {}) or {}
        entry_status = str(record.get("status", "needs-review"))
        entry_folder = str(record.get("library_folder", kb.default_library_folder(record, entry.name)))
        if folder and entry_folder != folder:
            continue
        if status and entry_status != status:
            continue
        entries.append({
            "entry_id": entry.name,
            "title": str(record.get("title", entry.name)),
            "subject": str(record.get("subject", "")),
            "grade": str(record.get("grade", "")),
            "status": entry_status,
            "folder": entry_folder,
            "knowledge_points": record.get("knowledge_points", []),
            "error_types": record.get("error_types", []),
            "updated_at": record.get("updated_at", ""),
        })
        if len(entries) >= limit:
            break
    entries.sort(key=lambda item: str(item["updated_at"]), reverse=True)
    return entries


def _read_reviews(entry: Path) -> dict[str, Any]:
    def review_status(name: str, key: str) -> str:
        data = kb.load_json(entry / name, {}) or {}
        return str(data.get("status", "")) if isinstance(data, dict) else ""

    answer = review_status("answer-review.json", "status")
    source = review_status("source-review.json", "status")
    viz = review_status("visualization-review.json", "status")
    pub_images = kb.load_json(entry / "publication-images.json", {}) or {}
    pub_status = str(pub_images.get("status", "")) if isinstance(pub_images, dict) else ""
    return {
        "source_review": source or "missing",
        "answer_review": answer or "missing",
        "visualization_review": viz or "missing",
        "publication_images": pub_status or "missing",
        "delivered": bool((entry / "delivery-manifest.json").exists()),
    }


def _get_entry(entry_id: str, include_teacher_answer: bool) -> dict[str, Any]:
    library = _current_library()
    entry = library / "entries" / entry_id
    if not entry.is_dir():
        raise FileNotFoundError(f"entry not found: {entry_id}")
    record = kb.load_json(entry / "record.json", {}) or {}
    problem = ""
    if (entry / "problem.md").exists():
        problem = (entry / "problem.md").read_text(encoding="utf-8")
    payload: dict[str, Any] = {
        "entry_id": entry_id,
        "title": str(record.get("title", entry.name)),
        "subject": str(record.get("subject", "")),
        "grade": str(record.get("grade", "")),
        "kind": str(record.get("kind", "error")),
        "status": str(record.get("status", "needs-review")),
        "answer_status": str(record.get("answer_status", "")),
        "folder": str(record.get("library_folder", kb.default_library_folder(record, entry.name))),
        "knowledge_points": record.get("knowledge_points", []),
        "error_types": record.get("error_types", []),
        "difficulty": record.get("difficulty", ""),
        "problem": problem,
        "reviews": _read_reviews(entry),
        "has_visualization": bool((entry / "physics-model.json").exists()),
        "student_answers": [name for name in ("solution.md", "student-solution.md") if (entry / name).exists()],
    }
    if include_teacher_answer:
        teacher = entry / "teacher-solution.md"
        payload["teacher_answer"] = teacher.read_text(encoding="utf-8") if teacher.exists() else ""
    return payload


def _retrieve(
    text: str,
    mode: str,
    top_k: int,
    ranking_policy: str,
    include_solution_snippets: bool,
) -> dict[str, Any]:
    library = _current_library()
    result = knowledge_store.query(
        library,
        text,
        mode=mode if mode in {"auto", "teaching", "raw"} else "auto",
        top_k=max(1, min(int(top_k), 20)),
        ranking_policy=(
            ranking_policy
            if ranking_policy in {"baseline", "multi-route", "intent-augmented"}
            else "baseline"
        ),
    )
    if result.get("status") != "ok":
        return result
    for item in result.get("results", []):
        if include_solution_snippets:
            continue
        item["matched_documents"] = [
            doc for doc in item.get("matched_documents", [])
            if doc.get("kind") in PUBLIC_DOCUMENT_KINDS
        ]
    return result


def _build_evidence_pack(
    entry_id: str,
    text: str,
    task_type: str,
    top_k: int,
    char_budget: int,
    selection_policy: str,
) -> dict[str, Any]:
    library = _current_library()
    return knowledge_store.build_agent_evidence(
        library,
        entry_id,
        text,
        task_type=(
            task_type
            if task_type in {"analysis.generate", "answer.revise", "visualization.model"}
            else "analysis.generate"
        ),
        top_k=max(1, min(int(top_k), 8)),
        char_budget=max(1000, min(int(char_budget), 20000)),
        selection_policy=selection_policy
        if selection_policy in {"baseline", "precision-gated-v1", "evidence-set-v2"}
        else "baseline",
    )


def _read_events(entry_id: str, limit: int) -> list[dict[str, Any]]:
    library = _current_library()
    target = knowledge_store.db_path(library)
    if not target.exists():
        return []
    connection = knowledge_store.connect_readonly(target)
    try:
        return knowledge_store._recent_events(connection, entry_id, limit=max(1, min(int(limit), 20)))
    finally:
        connection.close()


def _evaluator_summary(entry_id: str) -> dict[str, Any]:
    library = _current_library()
    entry = library / "entries" / entry_id
    if not entry.is_dir():
        raise FileNotFoundError(f"entry not found: {entry_id}")
    evaluation = kb.load_json(entry / "evaluation.json", {}) or {}
    return {
        "entry_id": entry_id,
        "status": str(evaluation.get("status", "missing")),
        "generated_at": evaluation.get("generated_at"),
        "scores": evaluation.get("scores", {}),
        "failure_reasons": evaluation.get("failure_reasons", []),
        "warning_reasons": evaluation.get("warning_reasons", []),
        "teacher_review_required": bool(evaluation.get("teacher_review_required", True)),
    }


def _library_stats() -> dict[str, Any]:
    library = _current_library()
    target = knowledge_store.db_path(library)
    dirty = bool((library / "indexes" / "wuli-memory.dirty.json").exists())
    if not target.exists():
        return {
            "library": library.name,
            "status": "unavailable",
            "reason": "knowledge-store-missing",
            "freshness": "missing",
        }
    connection = knowledge_store.connect_readonly(target)
    try:
        schema_row = connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        fts_row = connection.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()
        generated_row = connection.execute("SELECT value FROM meta WHERE key='generated_at'").fetchone()
        counts = {}
        for table in ("entry", "document", "evidence_unit", "candidate_event"):
            row = connection.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            counts[table] = int(row["c"]) if row else 0
    finally:
        connection.close()
    return {
        "library": library.name,
        "status": "ok",
        "schema_version": int(schema_row["value"]) if schema_row else -1,
        "fts5": bool(fts_row and fts_row["value"] == "1"),
        "generated_at": generated_row["value"] if generated_row else "",
        "freshness": "stale" if dirty else "current",
        "counts": counts,
    }


# ---------------------------------------------------------------------------
# MCP 工具
# ---------------------------------------------------------------------------

def _register_tools(mcp: Any) -> None:
    @mcp.tool()
    async def list_entries(
        folder: Annotated[str, "按本地文件夹过滤，例如“力学”；留空返回全部"] = "",
        status: Annotated[str, "按条目状态过滤（ready / needs-review 等）；留空返回全部"] = "",
        limit: Annotated[int, "最多返回条数（1-200）"] = 50,
    ) -> dict[str, Any]:
        """浏览错题库：返回条目 id、标题、状态、知识点与错因摘要。"""
        trace_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(_list_entries, folder, status, max(1, min(int(limit), 200)))
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("tool=list_entries trace_id=%s count=%d ms=%d", trace_id, len(data), duration_ms)
            return {"count": len(data), "entries": data}
        except Exception as exc:  # noqa: BLE001 — 错误契约：结构化返回，不抛
            logger.warning("tool=list_entries trace_id=%s error=%s", trace_id, exc)
            return _error("internal_error", f"list_entries failed: {exc}")

    @mcp.tool()
    async def get_entry(
        entry_id: Annotated[str, "条目 id（如 20260722-knowledge-store）"],
        include_teacher_answer: Annotated[bool, "是否返回教师版解析全文；默认 false（隐私保护）"] = False,
    ) -> dict[str, Any]:
        """读取单题的学生可读信息：题干、状态、复核与交付状态；默认不含教师版答案。"""
        trace_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(_get_entry, entry_id, include_teacher_answer)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("tool=get_entry trace_id=%s entry=%s ms=%d", trace_id, entry_id, duration_ms)
            return data
        except FileNotFoundError as exc:
            return _error("not_found", str(exc), entry_id=entry_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool=get_entry trace_id=%s error=%s", trace_id, exc)
            return _error("internal_error", f"get_entry failed: {exc}")

    @mcp.tool()
    async def retrieve(
        text: Annotated[str, "检索查询（支持自然语言，如“动量守恒 非弹性碰撞 求共同速度”）"],
        mode: Annotated[str, "auto / teaching / raw；teaching 会做口语到规范词的确定性扩展"] = "auto",
        top_k: Annotated[int, "返回候选条目数（1-20）"] = 5,
        ranking_policy: Annotated[str, "baseline（生产默认）/ multi-route / intent-augmented"] = "baseline",
        include_solution_snippets: Annotated[bool, "匹配片段是否包含解析正文；默认 false（公网安全）"] = False,
    ) -> dict[str, Any]:
        """RAG 检索：召回相似题，附条件审计、覆盖度与教学记忆；等价于工作台检索结果。"""
        trace_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(_retrieve, text, mode, top_k, ranking_policy, include_solution_snippets)
            result = _guard(data)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("tool=retrieve trace_id=%s status=%s ms=%d", trace_id, result.get("status"), duration_ms)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool=retrieve trace_id=%s error=%s", trace_id, exc)
            return _error("internal_error", f"retrieve failed: {exc}")

    @mcp.tool()
    async def build_evidence_pack(
        entry_id: Annotated[str, "当前题条目 id（会被排除在历史证据之外）"],
        text: Annotated[str, "当前题题干/答案文本，作为检索查询"],
        task_type: Annotated[str, "analysis.generate / answer.revise / visualization.model"] = "analysis.generate",
        top_k: Annotated[int, "最多引用数（1-8）"] = 3,
        char_budget: Annotated[int, "证据包序列化字符预算（1000-20000）"] = 8000,
        selection_policy: Annotated[str, "baseline / precision-gated-v1 / evidence-set-v2"] = "baseline",
    ) -> dict[str, Any]:
        """为 Agent 任务构造隐私最小化的历史证据包（相似题方法、易错点、既往教训）。"""
        trace_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(
                _build_evidence_pack, entry_id, text, task_type, top_k, char_budget, selection_policy
            )
            result = _guard(data, "knowledge-store-unavailable")
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("tool=build_evidence_pack trace_id=%s status=%s refs=%d ms=%d",
                        trace_id, result.get("status"), len(result.get("references", [])),
                        duration_ms)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool=build_evidence_pack trace_id=%s error=%s", trace_id, exc)
            return _error("internal_error", f"build_evidence_pack failed: {exc}")

    @mcp.tool()
    async def entry_events(
        entry_id: Annotated[str, "条目 id"],
        limit: Annotated[int, "最多返回事件数（1-20）"] = 5,
    ) -> dict[str, Any]:
        """读取单题近期教师/Agent 事件（任务类型、状态、失败原因），避免重复犯错。"""
        trace_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(_read_events, entry_id, limit)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("tool=entry_events trace_id=%s entry=%s count=%d ms=%d",
                        trace_id, entry_id, len(data), duration_ms)
            return {"entry_id": entry_id, "events": data}
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool=entry_events trace_id=%s error=%s", trace_id, exc)
            return _error("internal_error", f"entry_events failed: {exc}")

    @mcp.tool()
    async def evaluator_summary(
        entry_id: Annotated[str, "条目 id"],
    ) -> dict[str, Any]:
        """读取单题质量评价：六维评分、失败项、警告项与教师复核要求。"""
        trace_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(_evaluator_summary, entry_id)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("tool=evaluator_summary trace_id=%s entry=%s ms=%d", trace_id, entry_id, duration_ms)
            return data
        except FileNotFoundError as exc:
            return _error("not_found", str(exc), entry_id=entry_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool=evaluator_summary trace_id=%s error=%s", trace_id, exc)
            return _error("internal_error", f"evaluator_summary failed: {exc}")

    @mcp.tool()
    async def library_stats() -> dict[str, Any]:
        """读取知识库规模与索引健康度（freshness、FTS5、各表计数）。"""
        trace_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        try:
            data = await asyncio.to_thread(_library_stats)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("tool=library_stats trace_id=%s status=%s ms=%d", trace_id, data.get("status"), duration_ms)
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("tool=library_stats trace_id=%s error=%s", trace_id, exc)
            return _error("internal_error", f"library_stats failed: {exc}")


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def _resolve_library(cli_library: str | None, http_mode: bool) -> Path:
    if cli_library:
        library = Path(cli_library).expanduser().resolve()
        if not library.is_dir():
            raise SystemExit(f"库目录不存在: {library}")
        return library
    if http_mode:
        # 公网部署必须显式指定库，避免把真实错题库暴露到公网
        if DEFAULT_DEMO_LIBRARY.is_dir():
            logger.warning("未指定 WULI_LIBRARY，使用演示库 %s", DEFAULT_DEMO_LIBRARY)
            return DEFAULT_DEMO_LIBRARY
        raise SystemExit(
            "HTTP 公网模式必须显式指定知识库：设置环境变量 WULI_LIBRARY "
            "（或 --library），指向已构建索引的 student-error-library。"
        )
    return DEFAULT_LIBRARY


def _bearer_middleware(app: Any, api_key: str) -> Any:
    """可选 Bearer 鉴权：设置 WULI_MCP_API_KEY 后，非本地部署要求该请求头。"""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class _BearerAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.headers.get("authorization") != f"Bearer {api_key}":
                body = {"error": "unauthorized", "msg": "missing or invalid Authorization header"}
                return JSONResponse(body, status_code=401)
            return await call_next(request)

    return _BearerAuth(app)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdio", action="store_true", help="stdio 传输（默认，本地客户端）")
    parser.add_argument("--http", action="store_true", help="Streamable HTTP 传输（公网部署 / 赛事投稿）")
    parser.add_argument("--host", default=os.environ.get("WULI_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WULI_PORT", "8000")))
    parser.add_argument(
        "--library",
        default=os.environ.get("WULI_LIBRARY", ""),
        help="知识库目录（默认 student-error-library）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    http_mode = bool(args.http)
    _STATE["library"] = _resolve_library(args.library or None, http_mode)

    mcp = FastMCP(
        "wuli",
        instructions=(
            "悟理错题知识库的只读检索接口。所有工具都只读取本地知识库，"
            "不会修改条目、批准或发布。当前题干与教师指令优先于历史证据；"
            "历史证据只能用于核对方法、易错点与适用条件。"
        ),
        host=args.host,
        port=args.port,
        streamable_http_path="/mcp",
    )
    _register_tools(mcp)

    api_key = os.environ.get("WULI_MCP_API_KEY", "")
    if http_mode:
        app = mcp.streamable_http_app()
        if api_key:
            app = _bearer_middleware(app, api_key)
        import uvicorn

        logger.info("wuli-mcp http mode library=%s url=http://%s:%d/mcp", _STATE["library"], args.host, args.port)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    else:
        logger.info("wuli-mcp stdio mode library=%s", _STATE["library"])
        mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
