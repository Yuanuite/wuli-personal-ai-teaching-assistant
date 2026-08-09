"""MCP server 端到端测试：以 stdio 拉起真实服务，覆盖工具清单、happy path 与错误契约。"""

import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MCP_SERVER = ROOT / "deploy" / "mcp-server" / "mcp_server.py"
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
TEACHER_CONSOLE = ROOT / "teacher-console"
for path in (SCRIPTS, TEACHER_CONSOLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import candidate_archive  # noqa: E402
import evaluator  # noqa: E402
import kb  # noqa: E402
import knowledge_store  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


def seed_library(library: Path) -> None:
    kb.init_library(library)
    entry = library / "entries" / "20260810-mcp-test"
    assets = entry / "assets"
    assets.mkdir(parents=True)
    (assets / "original.png").write_bytes(b"source")
    solution = (
        "# 解析\n\n"
        "## 答案速览\n用动量守恒和能量守恒处理碰撞。\n\n"
        "## 详细解答\n先判断系统外力冲量可忽略，再列动量守恒式，最后用能量关系检查结果。\n\n"
        "## 一眼识别\n- 题型识别：动量守恒碰撞题\n"
        "- 最短主线：列动量守恒求共同速度，再用动能损失判断碰撞类型\n"
        "- 可用二级结论：非弹性碰撞后共同速度 v = m1v1/(m1+m2)**适用条件**：碰撞时间极短且系统外力冲量可忽略\n\n"
        "## 易错点\n不要把机械能守恒误用于非弹性碰撞。\n\n"
        "## 教师审计\n迁移任何二级结论前必须复核适用条件。\n"
    )
    for name, text in {
        "problem.md": "# 碰撞测试题\n\n小车与木块发生非弹性碰撞，求共同速度。",
        "solution.md": solution,
        "student-solution.md": solution,
        "teacher-solution.md": "教师版：动量守恒 + 恢复系数校验。" + solution,
    }.items():
        kb.write_text(entry / name, text)
    kb.write_json(
        entry / "record.json",
        {
            "schema_version": 1,
            "id": entry.name,
            "kind": "error",
            "status": "ready",
            "answer_status": "complete",
            "title": "动量守恒碰撞题",
            "subject": "高中物理",
            "grade": "高二",
            "knowledge_points": ["动量守恒", "非弹性碰撞"],
            "error_types": ["误用机械能守恒"],
            "difficulty": "3",
            "created_at": "2026-08-10T09:00:00+08:00",
            "updated_at": "2026-08-10T09:00:00+08:00",
            "source": {"sha256": hashlib.sha256(b"source").hexdigest(), "stored_files": ["assets/original.png"]},
            "ocr": {"engine": "test", "review_required": False},
            "source_review": {"status": "passed"},
        },
    )
    answer_review = {
        "schema_version": 1,
        "entry_id": entry.name,
        "status": "passed",
        "reviewer": "teacher",
        "reviewed_at": "2026-08-10T10:00:00+08:00",
        "answer_digest": kb.answer_artifact_digest(entry),
        "note": "checked",
    }
    kb.write_json(entry / "answer-review.json", answer_review)
    record = kb.load_json(entry / "record.json", {})
    record["answer_review"] = answer_review
    kb.write_json(entry / "record.json", record)
    evaluator.evaluate_entry(library, entry.name, write=True)
    candidate_archive.append_event(
        library, entry, task_type="answer.save", actor="teacher",
        event_type="manual-edit", status="saved", summary="教师调整解析",
        evaluation={}, changed_files=["student-solution.md"],
    )
    knowledge_store.rebuild(library)


async def call_tool(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    if result.isError:
        return {"_isError": True, "text": result.content[0].text if result.content else ""}
    return json.loads(result.content[0].text)


class McpServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Path(self.temp.name) / "student-error-library"
        seed_library(self.library)
        self.params = StdioServerParameters(
            command=sys.executable,
            args=[str(MCP_SERVER), "--stdio", "--library", str(self.library)],
            cwd=str(ROOT),
        )

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    async def _session(self):
        client = stdio_client(self.params)
        read, write = await client.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return client, session

    def test_tools_list_has_7_tools_with_valid_schemas(self):
        async def run():
            client, session = await self._session()
            try:
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                self.assertEqual(
                    set(names),
                    {
                        "list_entries", "get_entry", "retrieve", "build_evidence_pack",
                        "entry_events", "evaluator_summary", "library_stats",
                    },
                )
                for tool in tools.tools:
                    self.assertIsInstance(tool.inputSchema, dict)
                    self.assertIn("type", tool.inputSchema)
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())

    def test_list_entries_happy_path(self):
        async def run():
            client, session = await self._session()
            try:
                result = await call_tool(session, "list_entries", {"limit": 10})
                self.assertEqual(result["count"], 1)
                self.assertEqual(result["entries"][0]["title"], "动量守恒碰撞题")
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())

    def test_get_entry_hides_teacher_answer_by_default(self):
        async def run():
            client, session = await self._session()
            try:
                result = await call_tool(session, "get_entry", {"entry_id": "20260810-mcp-test"})
                self.assertNotIn("teacher_answer", result)
                self.assertEqual(result["title"], "动量守恒碰撞题")
                self.assertEqual(result["reviews"]["answer_review"], "passed")
                with_teacher = await call_tool(
                    session, "get_entry",
                    {"entry_id": "20260810-mcp-test", "include_teacher_answer": True},
                )
                self.assertIn("teacher_answer", with_teacher)
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())

    def test_get_entry_not_found_returns_structured_error(self):
        async def run():
            client, session = await self._session()
            try:
                result = await call_tool(session, "get_entry", {"entry_id": "no-such-entry"})
                self.assertEqual(result.get("error"), "not_found")
                self.assertIn("trace_id", result)
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())

    def test_retrieve_happy_path_and_public_snippet_filter(self):
        async def run():
            client, session = await self._session()
            try:
                result = await call_tool(session, "retrieve", {"text": "动量守恒 非弹性碰撞", "top_k": 3})
                self.assertEqual(result["status"], "ok")
                self.assertGreaterEqual(len(result["results"]), 1)
                for item in result["results"]:
                    for doc in item["matched_documents"]:
                        self.assertNotIn(doc["kind"], {"solution", "teacher_solution", "student_solution"})
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())

    def test_build_evidence_pack_excludes_self(self):
        async def run():
            client, session = await self._session()
            try:
                result = await call_tool(
                    session, "build_evidence_pack",
                    {"entry_id": "20260810-mcp-test", "text": "动量守恒 非弹性碰撞",
                     "task_type": "analysis.generate", "top_k": 3, "char_budget": 8000},
                )
                self.assertEqual(result["status"], "ready")
                self.assertIn("references", result)
                self.assertIn("context_budget", result)
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())

    def test_entry_events_and_evaluator_summary(self):
        async def run():
            client, session = await self._session()
            try:
                events = await call_tool(session, "entry_events", {"entry_id": "20260810-mcp-test", "limit": 5})
                self.assertGreaterEqual(len(events["events"]), 1)
                self.assertEqual(events["events"][0]["task_type"], "answer.save")
                summary = await call_tool(session, "evaluator_summary", {"entry_id": "20260810-mcp-test"})
                self.assertIn("scores", summary)
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())

    def test_library_stats_ok(self):
        async def run():
            client, session = await self._session()
            try:
                result = await call_tool(session, "library_stats", {})
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["freshness"], "current")
                self.assertGreaterEqual(result["counts"]["entry"], 1)
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())

    def test_missing_index_returns_structured_unavailable(self):
        async def run():
            # 纯空目录（不调 kb.init_library，它会在 indexes/ 下创建空 db）
            empty_library = Path(self.temp.name) / "empty-library"
            empty_library.mkdir(parents=True)
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(MCP_SERVER), "--stdio", "--library", str(empty_library)],
                cwd=str(ROOT),
            )
            client = stdio_client(params)
            read, write = await client.__aenter__()
            session = ClientSession(read, write)
            await session.__aenter__()
            await session.initialize()
            try:
                result = await call_tool(session, "retrieve", {"text": "动量守恒"})
                self.assertEqual(result.get("error"), "knowledge-store-missing")
                self.assertIn("trace_id", result)
                stats = await call_tool(session, "library_stats", {})
                self.assertEqual(stats["status"], "unavailable")
            finally:
                await session.__aexit__(None, None, None)
                await client.__aexit__(None, None, None)
        self._run(run())


if __name__ == "__main__":
    unittest.main()
