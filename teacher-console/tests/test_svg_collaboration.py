import copy
import unittest

import svg_collaboration
from visual_facts import normalize_payload


def canonical_facts():
    raw = {
        "schema": "wuli.visual-facts.v1",
        "source_fingerprint": "sha256:" + "a" * 64,
        "reviewed_text": "斜面上有物块",
        "printed_facts": ["物块从静止释放"],
        "diagram_facts": [],
        "handwriting": [],
        "uncertainties": [],
        "model_identity": {"model_id": "mimo-v2.5-flash", "provider": "openai-compatible"},
    }
    return normalize_payload(raw, str(raw["source_fingerprint"]))


def trace():
    return {
        "model_id": "deepseek-v4-flash-api",
        "provider": "openai-compatible",
        "generation_fingerprint": "sha256:" + "b" * 64,
    }


IDENTITY = {"model_id": "deepseek-v4-flash-api", "provider": "openai-compatible"}
SVG = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'


class SvgCollaborationTests(unittest.TestCase):
    def test_binds_both_inputs_and_svg(self):
        result = svg_collaboration.validate_and_bind_svg(SVG, canonical_facts(), trace(), IDENTITY)
        self.assertEqual(result["visual_facts_fingerprint"], canonical_facts()["fingerprint"])
        self.assertEqual(result["generation_fingerprint"], trace()["generation_fingerprint"])
        self.assertEqual(result["model_identity"], IDENTITY)
        self.assertEqual(result["safety"], {"status": "passed", "element_count": 2})

    def test_rejects_identity_mismatch(self):
        bad = trace()
        bad["model_id"] = "other"
        with self.assertRaises(ValueError):
            svg_collaboration.validate_and_bind_svg(SVG, canonical_facts(), bad, IDENTITY)

    def test_rejects_empty_identity(self):
        empty = {"model_id": "", "provider": "openai-compatible"}
        with self.assertRaises(ValueError):
            svg_collaboration.validate_and_bind_svg(
                SVG,
                canonical_facts(),
                {**empty, "generation_fingerprint": "sha256:" + "b" * 64},
                empty,
            )

    def test_rejects_noncanonical_or_tampered_facts(self):
        bad = canonical_facts()
        bad["reviewed_text"] = "tampered"
        with self.assertRaises(ValueError):
            svg_collaboration.validate_and_bind_svg(SVG, bad, trace(), IDENTITY)

    def test_inputs_are_not_mutated(self):
        facts = canonical_facts()
        generation = trace()
        before = copy.deepcopy((facts, generation, IDENTITY))
        svg_collaboration.validate_and_bind_svg(SVG, facts, generation, IDENTITY)
        self.assertEqual((facts, generation, IDENTITY), before)

    def test_rejects_unsafe_svg_constructs(self):
        unsafe = [
            "<svg><script/></svg>",
            '<svg onclick="x"/>',
            '<svg><image href="https://example.test/x"/></svg>',
            "<svg><foreignObject/></svg>",
            "<!DOCTYPE svg><svg/>",
        ]
        for svg in unsafe:
            with self.subTest(svg=svg), self.assertRaises(ValueError):
                svg_collaboration.validate_svg_safety(svg)

    def test_allows_local_marker_reference(self):
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg"><defs><marker id="a" markerWidth="2" '
            'markerHeight="2" refX="1" refY="1" orient="auto"><path d="M0 0L2 1"/></marker>'
            '</defs><line x1="0" y1="0" x2="2" y2="2" marker-end="url(#a)"/></svg>'
        )
        self.assertEqual(svg_collaboration.validate_svg_safety(svg)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
