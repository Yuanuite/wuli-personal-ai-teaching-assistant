"""Contract tests for the static-diagram application service (C4.1/C4.6)."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / ".claude" / "skills" / "manage-student-error-library" / "scripts"
CONSOLE = ROOT / "teacher-console"
for path in (SCRIPTS, CONSOLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import kb
import process_uploads
from diagram_application import BUILD_SCHEMA, build_diagram


class DiagramApplicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        self.entry = self.library / "entries" / "20260802-diagram"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        source_bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        (assets / "original.png").write_bytes(source_bytes)
        kb.write_text(self.entry / "problem.md", "# 题目\n\n测试题干。")
        kb.write_json(
            self.entry / "record.json",
            {
                "schema_version": 1,
                "id": self.entry.name,
                "kind": "error",
                "status": "needs-review",
                "answer_status": "pending",
                "title": "diagram test",
                "subject": "高中物理",
                "knowledge_points": ["测试"],
                "error_types": ["待确认"],
                "source": {
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "source_type": "png",
                    "stored_files": ["assets/original.png"],
                },
                "ocr": {"engine": "test", "review_required": False},
                "source_review": {"status": "passed"},
                "answer_review": {"status": "not-ready"},
            },
        )
        kb.write_json(
            self.entry / "ocr.json",
            {"engine": "test", "text": "ocr", "average_confidence": 0.5},
        )
        answer = (
            "# 解析（教师版）\n\n"
            "![关键关系示意图](assets/explanatory.svg)\n\n"
            "## 答案速览\n\n- 结论：$v=2\\ \\text{m/s}$。\n\n"
            "## 详细解答\n\n### 第 1 步：\n\n由动能定理 $F s=\\frac12 mv^2-\\frac12 mv_0^2$，"
            "代入数值得到最终速度。\n\n"
            "## 易错点\n\n- **错误表现**：忽略初速度；**纠正策略**：先写完整能量方程。\n\n"
            "## 30 秒自测\n\n为什么初速度不能省略？\n"
        )
        kb.write_text(self.entry / "student-solution.md", answer)
        kb.write_text(self.entry / "teacher-solution.md", answer)
        kb.write_text(self.entry / "solution.md", answer)
        # The answer references the explanatory SVG, so the answer digest
        # covers the diagram bytes (C4.6 depends on this).
        (assets / "explanatory.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>',
            encoding="utf-8",
        )
        kb.write_json(
            self.entry / "visual-facts.json",
            {
                "schema": "wuli.visual-facts.v1",
                "source_fingerprint": "sha256:" + "a" * 64,
                "reviewed_text": "reviewed",
                "printed_facts": [],
                "diagram_facts": [
                    {"id": "d1", "kind": "object", "statement": "box", "confidence": 0.9}
                ],
                "handwriting": [],
                "uncertainties": [],
                "model_identity": {"model_id": "mimo-v2.5-flash", "provider": "openai-compatible"},
            },
        )

    def _fake_gateway(self, status="completed"):
        def gateway(entry, *, routing_tier, model_config, canonical_entry=None):
            if status == "completed":
                (entry / "assets" / "explanatory.svg").write_text(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>',
                    encoding="utf-8",
                )
                (entry / "physics-diagram-scene.json").write_text(
                    json.dumps({"schema": "wuli.physics-diagram-scene.v1", "shapes": []}),
                    encoding="utf-8",
                )
                return {"status": "completed", "provider": "adapter", "model_id": "solver"}
            return {"status": "failed", "message": "hard gate failed", "failure_type": "diagram_failed"}

        return gateway

    def test_blocks_when_source_not_approved(self):
        record = kb.load_json(self.entry / "record.json", {})
        record["source_review"] = {"status": "needs-review"}
        kb.write_json(self.entry / "record.json", record)
        result = build_diagram(self.entry, library=self.library)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("source review", result["reason"])

    def test_blocks_when_answer_missing(self):
        (self.entry / "student-solution.md").unlink()
        result = build_diagram(self.entry, library=self.library)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("answer layer missing", result["reason"])

    def test_blocks_when_visual_facts_missing(self):
        (self.entry / "visual-facts.json").unlink()
        result = build_diagram(self.entry, library=self.library)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("visual-facts.json missing", result["reason"])

    def test_success_runs_gateway_and_records_build(self):
        result = build_diagram(
            self.entry,
            library=self.library,
            run_gateway=self._fake_gateway(),
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["schema"], BUILD_SCHEMA)
        self.assertIn("soft_review", result)
        build = kb.load_json(self.entry / "diagram-build.json", {})
        self.assertEqual(build["status"], "completed")

    def test_gateway_failure_is_reported(self):
        result = build_diagram(
            self.entry,
            library=self.library,
            run_gateway=self._fake_gateway(status="failed"),
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_type"], "diagram_failed")

    def test_diagram_change_invalidates_answer_approval(self):
        # Approve the answer first (digest of current artifacts).
        process_uploads.approve_answer(self.library, self.entry.name, "teacher", "ok")
        from agent_jobs import AgentJobManager  # noqa: F401  (import guard for server globals)

        # Building a diagram overwrites assets/explanatory.svg -> digest changes.
        build_diagram(self.entry, library=self.library, run_gateway=self._fake_gateway())
        state = process_uploads.pipeline_state(self.entry)
        self.assertEqual(state["state"], "needs-answer-review")


if __name__ == "__main__":
    unittest.main()
