import base64
import email.message
import json
import os
import sys
import tempfile
import unittest
from typing import cast
from unittest.mock import patch

# Ensure teacher-console directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_gateway import AgentGateway
from visual_extraction import VisualExtractionError, extract_visual_facts, run_vision_probe

VALID_FINGERPRINT = "sha256:" + "a" * 64


def make_config(**overrides):
    config = {
        "id": "mimo-v2.5-flash",
        "provider": "openai-compatible",
        "model": "mimo-v2.5",
        "base_url": "https://host/v1",
        "api_key": "secret-key",
        "timeout_seconds": 30,
        "remote": False,
        "traits": {"vision": True},
    }
    config.update(overrides)
    return config


def make_review_payload():
    return {
        "images": [],
        "ocr": {"text": "some ocr"},
        "subject": "test subject",
        "source_sha256": "abc123",
        "required_checks": ["check1"],
    }


def make_model_output(overrides=None):
    output = {
        "reviewed_text": "reviewed",
        "printed_facts": ["fact1"],
        "diagram_facts": [{"id": "d1", "kind": "region", "statement": "diagram statement", "confidence": 0.9}],
        "handwriting": ["handwritten"],
        "uncertainties": [],
    }
    if overrides:
        output.update(overrides)
    return output


def make_response(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestExtractVisualFacts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        image_path = os.path.join(self.tmpdir.name, "source.png")
        with open(image_path, "wb") as handle:
            handle.write(b"fake image bytes")
        self.config = make_config()
        self.review_payload = make_review_payload()
        self.review_payload["images"] = [image_path]
        self.expected_runtime_identity = {"model_id": "mimo-v2.5-flash", "provider": "openai-compatible"}

    def test_exact_resolver_call_and_trace(self):
        calls = []

        def resolver(kind, routing_tier="auto"):
            calls.append((kind, routing_tier))
            return self.config

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(json.dumps(make_model_output())))

        result = extract_visual_facts(
            self.review_payload,
            VALID_FINGERPRINT,
            model_resolver=resolver,
            expected_runtime_identity=self.expected_runtime_identity,
            allow_remote=True,
            urlopen=fake_urlopen,
            routing_tier="fast",
        )
        self.assertEqual(calls, [("vision", "fast")])
        self.assertEqual(result["trace"]["model_id"], "mimo-v2.5-flash")
        self.assertEqual(result["trace"]["provider"], "openai-compatible")
        self.assertEqual(result["trace"]["upstream_model"], "mimo-v2.5")
        self.assertEqual(result["trace"]["remote"], False)
        self.assertEqual(result["trace"]["source_fingerprint"], VALID_FINGERPRINT)

    def test_agent_gateway_owns_registry_resolution(self):
        calls = []

        def resolver(kind, model_id=None, routing_tier="auto"):
            calls.append((kind, model_id, routing_tier))
            return self.config

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(json.dumps(make_model_output())))

        result = AgentGateway(environ={}).extract_visual_facts(
            self.review_payload,
            VALID_FINGERPRINT,
            allow_remote=True,
            model_id="mimo-v2.5-flash",
            routing_tier="auto",
            model_resolver=resolver,
            urlopen=fake_urlopen,
        )
        self.assertEqual(
            calls,
            [("vision", "mimo-v2.5-flash", "auto")],
        )
        self.assertEqual(result["trace"]["model_id"], "mimo-v2.5-flash")

    def test_model_authored_identity_ignored(self):
        output = make_model_output()
        output["model_identity"] = {"model_id": "evil", "provider": "evil"}
        output["schema"] = "evil"
        output["fingerprint"] = "evil"
        output["status"] = "evil"
        output["extra"] = "evil"

        def resolver(kind, routing_tier="auto"):
            return self.config

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(json.dumps(output)))

        result = extract_visual_facts(
            self.review_payload,
            VALID_FINGERPRINT,
            model_resolver=resolver,
            expected_runtime_identity=self.expected_runtime_identity,
            allow_remote=True,
            urlopen=fake_urlopen,
        )
        self.assertEqual(result["visual_facts"]["model_identity"], self.expected_runtime_identity)
        self.assertEqual(result["visual_facts"]["schema"], "wuli.visual-facts.v1")
        self.assertEqual(result["visual_facts"]["source_fingerprint"], VALID_FINGERPRINT)
        self.assertNotIn("extra", result["visual_facts"])

    def test_uncertainty_and_low_confidence_gate_needs_source_review(self):
        output = make_model_output({
            "uncertainties": ["uncertain"],
            "diagram_facts": [{"id": "d1", "kind": "region", "statement": "s", "confidence": 0.4}],
        })

        def resolver(kind, routing_tier="auto"):
            return self.config

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(json.dumps(output)))

        result = extract_visual_facts(
            self.review_payload,
            VALID_FINGERPRINT,
            model_resolver=resolver,
            expected_runtime_identity=self.expected_runtime_identity,
            allow_remote=True,
            urlopen=fake_urlopen,
        )
        self.assertEqual(result["gate_result"]["status"], "needs-source-review")

    def test_fully_clean_facts_pass(self):
        output = make_model_output()

        def resolver(kind, routing_tier="auto"):
            return self.config

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(json.dumps(output)))

        result = extract_visual_facts(
            self.review_payload,
            VALID_FINGERPRINT,
            model_resolver=resolver,
            expected_runtime_identity=self.expected_runtime_identity,
            allow_remote=True,
            urlopen=fake_urlopen,
        )
        self.assertEqual(result["gate_result"]["status"], "passed")

    def test_route_mismatch(self):
        def resolver(kind, routing_tier="auto"):
            raise RuntimeError("no route")

        with self.assertRaises(VisualExtractionError):
            extract_visual_facts(
                self.review_payload,
                VALID_FINGERPRINT,
                model_resolver=resolver,
                expected_runtime_identity=self.expected_runtime_identity,
                allow_remote=True,
                urlopen=lambda req, timeout=None: None,
            )

    def test_remote_privacy_blocks_before_image_read_and_url_call(self):
        self.config["remote"] = True
        called = []

        def resolver(kind, routing_tier="auto"):
            return self.config

        def fake_urlopen(req, timeout=None):
            called.append("urlopen")
            return FakeResponse(make_response(json.dumps(make_model_output())))

        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(b"fake image")
            f.flush()
            payload = make_review_payload()
            payload["images"] = [f.name]
            with self.assertRaises(VisualExtractionError):
                extract_visual_facts(
                    payload,
                    VALID_FINGERPRINT,
                    model_resolver=resolver,
                    expected_runtime_identity=self.expected_runtime_identity,
                    allow_remote=False,
                    urlopen=fake_urlopen,
                )
        self.assertEqual(called, [])

    def test_non_vision_wrong_provider_rejection(self):
        bad_configs = [
            make_config(provider="other"),
            make_config(traits={"vision": False}),
        ]
        for bad in bad_configs:

            def resolver(kind, routing_tier="auto"):
                return bad

            with self.assertRaises(VisualExtractionError):
                extract_visual_facts(
                    self.review_payload,
                    VALID_FINGERPRINT,
                    model_resolver=resolver,
                    expected_runtime_identity=self.expected_runtime_identity,
                    allow_remote=True,
                    urlopen=lambda req, timeout=None: None,
                )

    def test_missing_bad_image(self):
        payload = make_review_payload()
        payload["images"] = ["/nonexistent/image.png"]

        def resolver(kind, routing_tier="auto"):
            return self.config

        with self.assertRaises(VisualExtractionError):
            extract_visual_facts(
                payload,
                VALID_FINGERPRINT,
                model_resolver=resolver,
                expected_runtime_identity=self.expected_runtime_identity,
                allow_remote=True,
                urlopen=lambda req, timeout=None: None,
            )
        payload["images"] = []
        with self.assertRaises(VisualExtractionError):
            extract_visual_facts(
                payload,
                VALID_FINGERPRINT,
                model_resolver=resolver,
                expected_runtime_identity=self.expected_runtime_identity,
                allow_remote=True,
                urlopen=lambda req, timeout=None: None,
            )

    def test_fenced_json_accepted(self):
        content = "```json\n" + json.dumps(make_model_output()) + "\n```"

        def resolver(kind, routing_tier="auto"):
            return self.config

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(content))

        result = extract_visual_facts(
            self.review_payload,
            VALID_FINGERPRINT,
            model_resolver=resolver,
            expected_runtime_identity=self.expected_runtime_identity,
            allow_remote=True,
            urlopen=fake_urlopen,
        )
        self.assertEqual(result["gate_result"]["status"], "passed")

    def test_surrounding_prose_rejected(self):
        content = "Here is the result: " + json.dumps(make_model_output())

        def resolver(kind, routing_tier="auto"):
            return self.config

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(content))

        with self.assertRaises(VisualExtractionError):
            extract_visual_facts(
                self.review_payload,
                VALID_FINGERPRINT,
                model_resolver=resolver,
                expected_runtime_identity=self.expected_runtime_identity,
                allow_remote=True,
                urlopen=fake_urlopen,
            )

    def test_malformed_endpoint_response(self):
        bad_responses = [
            "not json",
            json.dumps({"choices": []}),
            json.dumps({"choices": [{"message": {}}]}),
        ]
        for bad in bad_responses:

            def resolver(kind, routing_tier="auto"):
                return self.config

            def fake_urlopen(req, timeout=None):
                return FakeResponse(bad)

            with self.assertRaises(VisualExtractionError):
                extract_visual_facts(
                    self.review_payload,
                    VALID_FINGERPRINT,
                    model_resolver=resolver,
                    expected_runtime_identity=self.expected_runtime_identity,
                    allow_remote=True,
                    urlopen=fake_urlopen,
                )

    def test_key_excluded_from_body_trace_error(self):
        captured_body = {}

        def resolver(kind, routing_tier="auto"):
            return self.config

        def fake_urlopen(req, timeout=None):
            captured_body["body"] = json.loads(req.data.decode())
            return FakeResponse(make_response(json.dumps(make_model_output())))

        result = extract_visual_facts(
            self.review_payload,
            VALID_FINGERPRINT,
            model_resolver=resolver,
            expected_runtime_identity=self.expected_runtime_identity,
            allow_remote=True,
            urlopen=fake_urlopen,
        )
        self.assertNotIn("api_key", captured_body["body"])
        self.assertNotIn("api_key", json.dumps(result))
        prompt = captured_body["body"]["messages"][0]["content"][0]["text"]
        self.assertIn("region, object, arrow, label, boundary, geometry", prompt)
        self.assertIn("unique non-empty string", prompt)

        # Error path
        def bad_urlopen(req, timeout=None):
            raise RuntimeError("boom")

        with self.assertRaises(VisualExtractionError) as ctx:
            extract_visual_facts(
                self.review_payload,
                VALID_FINGERPRINT,
                model_resolver=resolver,
                expected_runtime_identity=self.expected_runtime_identity,
                allow_remote=True,
                urlopen=bad_urlopen,
            )
        self.assertNotIn("secret-key", str(ctx.exception))

    def test_input_remains_unchanged(self):
        import copy

        original = copy.deepcopy(self.review_payload)

        def resolver(kind, routing_tier="auto"):
            return self.config

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(json.dumps(make_model_output())))

        extract_visual_facts(
            self.review_payload,
            VALID_FINGERPRINT,
            model_resolver=resolver,
            expected_runtime_identity=self.expected_runtime_identity,
            allow_remote=True,
            urlopen=fake_urlopen,
        )
        self.assertEqual(self.review_payload, original)


class TestVisionProbe(unittest.TestCase):
    """wuli.vision-probe.v1: same endpoint/image contract as production."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.image_path = os.path.join(self.tmpdir.name, "probe.png")
        with open(self.image_path, "wb") as handle:
            handle.write(b"fake probe image bytes")
        self.config = make_config()
        self.config["traits"] = {"vision": True}

    def test_probe_passes_with_ok_response(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(make_response('{"ok": true}'))

        result = run_vision_probe(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["schema"], "wuli.vision-probe.v1")
        self.assertEqual(result["model_id"], "mimo-v2.5-flash")
        self.assertEqual(result["upstream_model"], "mimo-v2.5")
        self.assertTrue(captured["url"].endswith("/chat/completions"))
        content = captured["body"]["messages"][0]["content"]
        self.assertTrue(any(item.get("type") == "image_url" for item in content))
        self.assertEqual(captured["body"]["model"], "mimo-v2.5")

    def test_probe_reports_http_404_as_failed(self):
        from urllib.error import HTTPError

        def fake_urlopen(req, timeout=None):
            raise HTTPError(req.full_url, 404, "Not Found", email.message.Message(), None)

        result = run_vision_probe(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("404", result["reason"])

    def test_probe_reports_malformed_response_as_failed(self):
        def fake_urlopen(req, timeout=None):
            return FakeResponse("not json at all")

        result = run_vision_probe(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("malformed", result["reason"])

    def test_probe_fails_when_model_lacks_vision_trait(self):
        config = make_config()
        config["traits"] = {"vision": False}
        result = run_vision_probe(
            config,
            self.image_path,
            urlopen=lambda req, timeout=None: FakeResponse(make_response('{"ok": true}')),
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("vision trait", result["reason"])

    def test_probe_fails_when_remote_not_allowed(self):
        config = make_config(remote=True)
        result = run_vision_probe(
            config,
            self.image_path,
            urlopen=lambda req, timeout=None: FakeResponse(make_response('{"ok": true}')),
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("disabled", result["reason"])

    def test_probe_never_reads_student_data(self):
        # A missing probe image fails closed without touching the network.
        result = run_vision_probe(
            self.config,
            "/nonexistent/probe.png",
            urlopen=lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("must not be called")),
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("missing", result["reason"])


if __name__ == "__main__":
    unittest.main()
