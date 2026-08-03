import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

CONSOLE_DIR = Path(__file__).resolve().parents[1]
if str(CONSOLE_DIR) not in sys.path:
    sys.path.insert(0, str(CONSOLE_DIR))

from visual_facts import evaluate_gate, normalize_payload


def make_raw(**overrides):
    raw = {
        "schema": "wuli.visual-facts.v1",
        "source_fingerprint": "sha256:" + "a" * 64,
        "reviewed_text": "Reviewed text",
        "printed_facts": ["fact1", "fact2"],
        "diagram_facts": [
            {"id": "f1", "kind": "region", "statement": "Region A", "confidence": 0.9},
            {"id": "f2", "kind": "arrow", "statement": "Arrow B", "confidence": 0.7},
        ],
        "handwriting": ["note1"],
        "uncertainties": [],
        "model_identity": {"model_id": "model-x", "provider": "provider-y"},
    }
    raw.update(overrides)
    return raw


def make_canonical(**overrides):
    raw = make_raw(**overrides)
    return normalize_payload(raw, raw["source_fingerprint"])


class TestNormalizePayload(unittest.TestCase):
    def test_valid_normalization(self):
        raw = make_raw()
        norm = normalize_payload(raw, raw["source_fingerprint"])
        self.assertEqual(norm["schema"], "wuli.visual-facts.v1")
        self.assertEqual(norm["source_fingerprint"], raw["source_fingerprint"])
        self.assertEqual(norm["reviewed_text"], raw["reviewed_text"])
        self.assertEqual(norm["printed_facts"], raw["printed_facts"])
        self.assertEqual(norm["diagram_facts"], raw["diagram_facts"])
        self.assertEqual(norm["handwriting"], raw["handwriting"])
        self.assertEqual(norm["uncertainties"], raw["uncertainties"])
        self.assertEqual(norm["model_identity"], raw["model_identity"])
        self.assertIn("fingerprint", norm)
        self.assertTrue(norm["fingerprint"].startswith("sha256:"))
        self.assertEqual(len(norm["fingerprint"][len("sha256:") :]), 64)

    def test_exact_sha_format(self):
        raw = make_raw()
        norm = normalize_payload(raw, raw["source_fingerprint"])
        self.assertRegex(norm["fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(norm["source_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        raw["source_fingerprint"] = "sha256:" + "A" * 64
        with self.assertRaises(ValueError):
            normalize_payload(raw, raw["source_fingerprint"])

    def test_source_fingerprint_mismatch(self):
        raw = make_raw()
        with self.assertRaises(ValueError):
            normalize_payload(raw, "sha256:" + "b" * 64)

    def test_unknown_fields_rejected(self):
        raw = make_raw(extra_field="x")
        with self.assertRaises(ValueError):
            normalize_payload(raw, raw["source_fingerprint"])

    def test_invalid_diagram_fact_kind(self):
        raw = make_raw()
        raw["diagram_facts"][0]["kind"] = "invalid"
        with self.assertRaises(ValueError):
            normalize_payload(raw, raw["source_fingerprint"])

    def test_invalid_confidence_bool(self):
        raw = make_raw()
        raw["diagram_facts"][0]["confidence"] = True
        with self.assertRaises(ValueError):
            normalize_payload(raw, raw["source_fingerprint"])

    def test_uncertainties_preserved(self):
        raw = make_raw(uncertainties=["uncertain"])
        norm = normalize_payload(raw, raw["source_fingerprint"])
        self.assertEqual(norm["uncertainties"], ["uncertain"])

    def test_idempotence(self):
        raw = make_raw()
        norm1 = normalize_payload(raw, raw["source_fingerprint"])
        norm2 = normalize_payload(norm1, raw["source_fingerprint"])
        self.assertEqual(norm1, norm2)

    def test_supplied_fingerprint_verification(self):
        raw = make_raw()
        norm = normalize_payload(raw, raw["source_fingerprint"])
        raw_with_fp = dict(raw)
        raw_with_fp["fingerprint"] = norm["fingerprint"]
        norm2 = normalize_payload(raw_with_fp, raw["source_fingerprint"])
        self.assertEqual(norm, norm2)

    def test_supplied_fingerprint_mismatch(self):
        raw = make_raw()
        raw["fingerprint"] = "sha256:" + "c" * 64
        with self.assertRaises(ValueError):
            normalize_payload(raw, raw["source_fingerprint"])

    def test_stable_fingerprint(self):
        raw = make_raw()
        norm1 = normalize_payload(raw, raw["source_fingerprint"])
        norm2 = normalize_payload(raw, raw["source_fingerprint"])
        self.assertEqual(norm1["fingerprint"], norm2["fingerprint"])

    def test_missing_required_field(self):
        raw = make_raw()
        del raw["reviewed_text"]
        with self.assertRaises(ValueError):
            normalize_payload(raw, raw["source_fingerprint"])

    def test_wrong_schema(self):
        raw = make_raw(schema="wrong")
        with self.assertRaises(ValueError):
            normalize_payload(raw, raw["source_fingerprint"])

    def test_duplicate_diagram_fact_ids(self):
        raw = make_raw()
        raw["diagram_facts"].append(copy.deepcopy(raw["diagram_facts"][0]))
        with self.assertRaises(ValueError):
            normalize_payload(raw, raw["source_fingerprint"])

    def test_nested_copy_isolation(self):
        raw = make_raw()
        norm = normalize_payload(raw, raw["source_fingerprint"])
        raw["diagram_facts"][0]["confidence"] = 0.1
        raw["model_identity"]["model_id"] = "changed"
        raw["printed_facts"].append("changed")
        self.assertEqual(norm["diagram_facts"][0]["confidence"], 0.9)
        self.assertEqual(norm["model_identity"]["model_id"], "model-x")
        self.assertEqual(norm["printed_facts"], ["fact1", "fact2"])


class TestEvaluateGate(unittest.TestCase):
    def test_passed_gate(self):
        canonical = make_canonical()
        result = evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        self.assertEqual(result["schema"], "wuli.visual-facts-gate-result.v1")
        self.assertEqual(result["visual_facts_fingerprint"], canonical["fingerprint"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["thresholds"], {"minimum_fact_confidence": 0.6})

    def test_source_mismatch_reason(self):
        canonical = make_canonical()
        result = evaluate_gate(
            canonical,
            "sha256:" + "d" * 64,
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        self.assertEqual(result["status"], "needs-source-review")
        self.assertIn("source-mismatch", [r["code"] for r in result["reasons"]])

    def test_empty_reviewed_text_reason(self):
        canonical = make_canonical(reviewed_text="")
        result = evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        self.assertEqual(result["status"], "needs-source-review")
        self.assertIn("empty-reviewed-text", [r["code"] for r in result["reasons"]])

    def test_uncertainty_present_reason(self):
        canonical = make_canonical(uncertainties=["uncertain"])
        result = evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        self.assertEqual(result["status"], "needs-source-review")
        self.assertIn("uncertainty-present", [r["code"] for r in result["reasons"]])

    def test_low_confidence_reason(self):
        canonical = make_canonical()
        canonical["diagram_facts"][0]["confidence"] = 0.5
        # Recompute fingerprint after mutation
        evidence = {k: v for k, v in canonical.items() if k != "fingerprint"}
        canonical["fingerprint"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        )
        result = evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        self.assertEqual(result["status"], "needs-source-review")
        low = [r for r in result["reasons"] if r["code"] == "low-confidence"]
        self.assertEqual(len(low), 1)
        self.assertEqual(low[0]["fact_id"], "f1")

    def test_low_confidence_boundary(self):
        canonical = make_canonical()
        canonical["diagram_facts"][0]["confidence"] = 0.6
        evidence = {k: v for k, v in canonical.items() if k != "fingerprint"}
        canonical["fingerprint"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        )
        result = evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        self.assertEqual(result["status"], "passed")

    def test_multiple_reasons(self):
        canonical = make_canonical(reviewed_text="", uncertainties=["uncertain"])
        canonical["diagram_facts"][0]["confidence"] = 0.5
        evidence = {k: v for k, v in canonical.items() if k != "fingerprint"}
        canonical["fingerprint"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        )
        result = evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        codes = [r["code"] for r in result["reasons"]]
        self.assertIn("empty-reviewed-text", codes)
        self.assertIn("uncertainty-present", codes)
        self.assertIn("low-confidence", codes)

    def test_route_mismatch_reason(self):
        canonical = make_canonical()
        result = evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-z", "provider": "provider-y"},
        )
        self.assertEqual(result["status"], "needs-source-review")
        self.assertIn("route-mismatch", [r["code"] for r in result["reasons"]])

    def test_route_separation_from_artifact_model_identity(self):
        canonical = make_canonical()
        canonical["model_identity"] = {"model_id": "model-x", "provider": "provider-y"}
        evidence = {k: v for k, v in canonical.items() if k != "fingerprint"}
        canonical["fingerprint"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest()
        )
        result = evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        self.assertEqual(result["status"], "passed")

    def test_input_immutability(self):
        canonical = make_canonical()
        original = copy.deepcopy(canonical)
        evaluate_gate(
            canonical,
            canonical["source_fingerprint"],
            {"model_id": "model-x", "provider": "provider-y"},
            {"model_id": "model-x", "provider": "provider-y"},
        )
        self.assertEqual(canonical, original)

    def test_missing_canonical_fingerprint(self):
        canonical = make_canonical()
        del canonical["fingerprint"]
        with self.assertRaises(ValueError):
            evaluate_gate(
                canonical,
                canonical["source_fingerprint"],
                {"model_id": "model-x", "provider": "provider-y"},
                {"model_id": "model-x", "provider": "provider-y"},
            )

    def test_unknown_canonical_field(self):
        canonical = make_canonical()
        canonical["extra"] = "x"
        with self.assertRaises(ValueError):
            evaluate_gate(
                canonical,
                canonical["source_fingerprint"],
                {"model_id": "model-x", "provider": "provider-y"},
                {"model_id": "model-x", "provider": "provider-y"},
            )

    def test_expected_fingerprint_format(self):
        canonical = make_canonical()
        with self.assertRaises(ValueError):
            evaluate_gate(
                canonical,
                "invalid",
                {"model_id": "model-x", "provider": "provider-y"},
                {"model_id": "model-x", "provider": "provider-y"},
            )


if __name__ == "__main__":
    unittest.main()
