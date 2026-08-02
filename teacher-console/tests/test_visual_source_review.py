import copy
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / ".claude/skills/manage-student-error-library/scripts").is_dir()
)
SCRIPTS = ROOT / ".claude/skills/manage-student-error-library/scripts"
CONSOLE = ROOT / "teacher-console"
for path in (SCRIPTS, CONSOLE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import kb
import source_review
from visual_facts import evaluate_gate, normalize_payload
from visual_source_review import stage_visual_extraction


class TestStageVisualExtraction(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.entry = pathlib.Path(self.temp.name) / "test-entry"
        self.entry.mkdir()
        (self.entry / "source.png").write_bytes(b"image")
        self.record = {
            "source": {"sha256": "source", "stored_files": ["source.png"]},
            "ocr": {"review_required": True},
            "source_review": {"status": "needs-review"},
        }
        self.ocr = {"text": "OCR text"}
        kb.write_json(self.entry / "record.json", self.record)
        kb.write_json(self.entry / "ocr.json", self.ocr)
        (self.entry / "unrelated.txt").write_text("unchanged", encoding="utf-8")

    def extraction(self, *, uncertainties=None, confidence=0.9, gate_status=None):
        source_fp = "sha256:" + source_review.input_digest(
            self.entry, self.record, self.ocr
        )
        raw = {
            "schema": "wuli.visual-facts.v1",
            "source_fingerprint": source_fp,
            "reviewed_text": "Reviewed text",
            "printed_facts": ["Printed fact"],
            "diagram_facts": [
                {
                    "id": "vf1",
                    "kind": "region",
                    "statement": "Diagram statement",
                    "confidence": confidence,
                }
            ],
            "handwriting": ["Student note"],
            "uncertainties": list(uncertainties or []),
            "model_identity": {
                "model_id": "mimo-v2.5-flash",
                "provider": "openai-compatible",
            },
        }
        facts = normalize_payload(raw, source_fp)
        identity = {
            "model_id": "mimo-v2.5-flash",
            "provider": "openai-compatible",
        }
        gate = evaluate_gate(facts, source_fp, identity, identity)
        if gate_status is not None:
            gate["status"] = gate_status
        return {
            "visual_facts": facts,
            "gate_result": gate,
            "trace": {
                **identity,
                "upstream_model": "mimo-v2.5-flash",
                "remote": True,
                "source_fingerprint": source_fp,
            },
        }

    def test_clean_gate_still_stages_needs_review(self):
        extraction = self.extraction()
        report = stage_visual_extraction(self.entry, extraction)
        self.assertEqual(report["status"], "needs-review")
        self.assertEqual(report["visual_gate_status"], "passed")
        record = kb.load_json(self.entry / "record.json", {})
        self.assertEqual(record["source_review"]["status"], "needs-review")
        self.assertTrue(record["ocr"]["review_required"])
        self.assertEqual(extraction, self.extraction())

    def test_uncertainty_is_preserved(self):
        report = stage_visual_extraction(
            self.entry, self.extraction(uncertainties=["箭头方向不清"])
        )
        self.assertEqual(report["visual_gate_status"], "needs-source-review")
        self.assertEqual(report["uncertainties"], ["箭头方向不清"])
        self.assertIn("箭头方向不清", (self.entry / "source-review.md").read_text())

    def test_problem_contains_reviewed_text_and_diagram_statement(self):
        stage_visual_extraction(self.entry, self.extraction())
        problem = (self.entry / "problem.md").read_text(encoding="utf-8")
        self.assertIn("Reviewed text", problem)
        self.assertIn("Diagram statement", problem)

    def test_fingerprint_mismatches_reject_before_writes(self):
        mutations = []
        one = self.extraction()
        one["visual_facts"]["fingerprint"] = "sha256:" + "0" * 64
        mutations.append(one)
        two = self.extraction()
        two["gate_result"]["visual_facts_fingerprint"] = "sha256:" + "1" * 64
        mutations.append(two)
        three = self.extraction()
        three["trace"]["source_fingerprint"] = "sha256:" + "2" * 64
        mutations.append(three)
        for candidate in mutations:
            with self.assertRaises(ValueError):
                stage_visual_extraction(self.entry, candidate)
            self.assertFalse((self.entry / "visual-facts.json").exists())

    def test_malformed_schema_status_and_trace_reject(self):
        candidates = []
        one = self.extraction()
        one["visual_facts"]["schema"] = "wrong"
        candidates.append(one)
        two = self.extraction()
        two["gate_result"]["status"] = "approved"
        candidates.append(two)
        three = self.extraction()
        del three["trace"]["model_id"]
        candidates.append(three)
        for candidate in candidates:
            with self.assertRaises(ValueError):
                stage_visual_extraction(self.entry, candidate)

    def test_no_approval_unrelated_change_or_input_mutation(self):
        extraction = self.extraction()
        before = copy.deepcopy(extraction)
        with patch(
            "source_review.approve_source",
            side_effect=AssertionError("must not approve"),
        ):
            stage_visual_extraction(self.entry, extraction)
        self.assertEqual(extraction, before)
        self.assertEqual(
            (self.entry / "unrelated.txt").read_text(encoding="utf-8"),
            "unchanged",
        )
        self.assertFalse((self.entry / "pipeline.json").exists())


if __name__ == "__main__":
    unittest.main()
