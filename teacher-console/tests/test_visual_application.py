"""Contract tests for the shared visual-extract application service (C2.1/C2.5).

Verifies web and CLI share one orchestrator producing ``VisualExtractOutcome.v1``
and a redacted call ledger, and that failures fail closed without staging facts.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / ".claude/skills/manage-student-error-library/scripts").is_dir()
)
SCRIPTS = ROOT / ".claude/skills/manage-student-error-library/scripts"
CONSOLE = ROOT / "teacher-console"
for path in (SCRIPTS, CONSOLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import kb
import source_review
from visual_application import OUTCOME_SCHEMA, run_visual_extract
from visual_facts import evaluate_gate, normalize_payload


class _StubGateway:
    def __init__(self, extraction=None, error=None):
        self.extraction = extraction
        self.error = error
        self.calls = []

    def extract_visual_facts(
        self,
        payload,
        source_fingerprint,
        *,
        allow_remote,
        routing_tier="auto",
        model_id=None,
    ):
        self.calls.append({
            "allow_remote": allow_remote,
            "routing_tier": routing_tier,
            "model_id": model_id,
            "fingerprint": source_fingerprint,
        })
        if self.error is not None:
            raise self.error
        return self.extraction


class VisualApplicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = Path(self.temp.name) / "library"
        kb.init_library(self.library)
        kb.write_json(
            self.library / "config.json",
            {
                "schema_version": 1,
                "privacy": {"allow_remote_visual_review": True},
            },
        )
        self.entry = self.library / "entries" / "20260802-visual-app"
        assets = self.entry / "assets"
        assets.mkdir(parents=True)
        (assets / "original.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        self.record = {
            "schema_version": 1,
            "id": self.entry.name,
            "kind": "error",
            "status": "needs-review",
            "answer_status": "pending",
            "title": "visual app test",
            "subject": "高中物理",
            "knowledge_points": ["测试"],
            "error_types": ["待确认"],
            "source": {
                "sha256": "x" * 64,
                "source_type": "png",
                "stored_files": ["assets/original.png"],
            },
            "ocr": {"engine": "test", "review_required": True},
            "source_review": {"status": "needs-review"},
        }
        kb.write_json(self.entry / "record.json", self.record)
        kb.write_json(
            self.entry / "ocr.json",
            {"engine": "test", "text": "ocr", "average_confidence": 0.5},
        )
        kb.write_text(self.entry / "problem.md", "# 题目\n\n测试题干。")

    def _extraction(self):
        fp = "sha256:" + source_review.input_digest(self.entry, self.record, kb.load_json(self.entry / "ocr.json", {}))
        identity = {"model_id": "mimo-v2.5-flash", "provider": "openai-compatible"}
        raw = {
            "schema": "wuli.visual-facts.v1",
            "source_fingerprint": fp,
            "reviewed_text": "Reviewed text",
            "printed_facts": ["fact"],
            "diagram_facts": [{"id": "v1", "kind": "region", "statement": "s", "confidence": 0.9}],
            "handwriting": [],
            "uncertainties": [],
            "model_identity": identity,
        }
        facts = normalize_payload(raw, fp)
        gate = evaluate_gate(facts, fp, identity, identity)
        return {
            "visual_facts": facts,
            "gate_result": gate,
            "trace": {**identity, "upstream_model": "mimo-v2.5", "source_fingerprint": fp},
        }

    def test_success_stages_facts_and_writes_redacted_ledger(self):
        outcome = run_visual_extract(
            self.entry,
            library=self.library,
            routing_tier="economy",
            gateway=_StubGateway(extraction=self._extraction()),
        )
        self.assertEqual(outcome["schema"], OUTCOME_SCHEMA)
        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(outcome["model_id"], "mimo-v2.5-flash")
        self.assertEqual(outcome["upstream_model"], "mimo-v2.5")
        self.assertTrue(outcome["input_fingerprint"].startswith("sha256:"))
        self.assertTrue(outcome["output_fingerprint"].startswith("sha256:"))
        self.assertTrue((self.entry / "visual-facts.json").is_file())
        self.assertTrue((self.entry / "visual-facts-gate.json").is_file())
        ledger = json.loads((self.entry / "visual-extract-request.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["schema"], OUTCOME_SCHEMA)
        blob = json.dumps(ledger, ensure_ascii=False)
        for forbidden in ("api_key", "sk-", "data:", "Bearer", self.temp.name):
            self.assertNotIn(forbidden, blob)

    def test_failure_fails_closed_with_durable_reason(self):
        outcome = run_visual_extract(
            self.entry,
            library=self.library,
            gateway=_StubGateway(error=ValueError("没有可用的视觉模型路由")),
        )
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["failure_type"], "visual_extraction_failed")
        self.assertIn("没有可用的视觉模型路由", outcome["message"])
        self.assertFalse((self.entry / "visual-facts.json").exists())
        ledger = json.loads((self.entry / "visual-extract-request.json").read_text(encoding="utf-8"))
        self.assertEqual(ledger["status"], "failed")
        record = kb.load_json(self.entry / "record.json", {})
        self.assertTrue(record["ocr"]["review_required"])

    def test_privacy_flag_passed_to_gateway(self):
        gateway = _StubGateway(extraction=self._extraction())
        run_visual_extract(self.entry, library=self.library, gateway=gateway)
        self.assertTrue(gateway.calls[0]["allow_remote"])
        self.assertEqual(gateway.calls[0]["routing_tier"], "auto")
        self.assertIsNone(gateway.calls[0]["model_id"])


if __name__ == "__main__":
    unittest.main()
