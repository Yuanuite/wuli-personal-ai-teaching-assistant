import json
import os
import sys
import tempfile
import unittest
from urllib.error import HTTPError, URLError

# Ensure teacher-console directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diagram_visual_review import run_diagram_visual_review


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


def make_suggestions():
    return [
        {"code": "label-collision", "severity": "warning", "message": "标签 A 与箭头重叠"},
        {"code": "legend-missing", "severity": "suggestion", "message": "建议补充图例"},
    ]


def make_review_output(overrides=None):
    output = {"suggestions": make_suggestions()}
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


class TestDiagramVisualReview(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.image_path = os.path.join(self.tmpdir.name, "diagram.png")
        with open(self.image_path, "wb") as handle:
            handle.write(b"fake rendered diagram bytes")
        self.config = make_config()

    def test_review_passes_with_valid_suggestions(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(make_response(json.dumps(make_review_output())))

        result = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["schema"], "wuli.diagram-visual-review.v1")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["suggestions"], make_suggestions())
        self.assertEqual(result["model_id"], "mimo-v2.5-flash")
        self.assertEqual(result["upstream_model"], "mimo-v2.5")
        self.assertIn("checked_at", result)
        self.assertIn("reason", result)
        # Same endpoint/image-message contract as production extraction
        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual(captured["body"]["model"], "mimo-v2.5")
        content = captured["body"]["messages"][0]["content"]
        self.assertTrue(any(item.get("type") == "image_url" for item in content))

    def test_review_passes_with_empty_suggestions(self):
        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response('{"suggestions": []}'))

        result = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["suggestions"], [])

    def test_review_reports_http_404_as_failed(self):
        def fake_urlopen(req, timeout=None):
            raise HTTPError(req.full_url, 404, "Not Found", {}, None)

        result = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("404", result["reason"])

    def test_review_reports_network_error_as_failed(self):
        def fake_urlopen(req, timeout=None):
            raise URLError("connection refused")

        result = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("network", result["reason"])

    def test_review_reports_invalid_json_as_failed(self):
        # Endpoint-level garbage
        def fake_urlopen(req, timeout=None):
            return FakeResponse("not json at all")

        result = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("malformed", result["reason"])

        # Model content that is not valid JSON
        def fake_urlopen2(req, timeout=None):
            return FakeResponse(make_response("not json"))

        result = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen2,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("malformed", result["reason"])

    def test_review_fails_on_invalid_suggestion_shape(self):
        bad_outputs = [
            {"suggestions": "not a list"},
            {"suggestions": [{"severity": "warning", "message": "missing code"}]},
            {"suggestions": [{"code": "c", "severity": "fatal", "message": "bad severity"}]},
            {"suggestions": [{"code": "c", "severity": "warning"}]},
        ]
        for bad in bad_outputs:

            def fake_urlopen(req, timeout=None, _bad=bad):
                return FakeResponse(make_response(json.dumps(_bad)))

            result = run_diagram_visual_review(
                self.config,
                self.image_path,
                urlopen=fake_urlopen,
                allow_remote=False,
            )
            self.assertEqual(result["status"], "failed")

    def test_review_blocked_when_model_lacks_vision_trait(self):
        config = make_config(traits={"vision": False})
        result = run_diagram_visual_review(
            config,
            self.image_path,
            urlopen=lambda req, timeout=None: None,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("vision trait", result["reason"])
        self.assertEqual(result["suggestions"], [])

    def test_review_blocked_when_provider_not_openai_compatible(self):
        config = make_config(provider="other")
        result = run_diagram_visual_review(
            config,
            self.image_path,
            urlopen=lambda req, timeout=None: None,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("provider", result["reason"])

    def test_review_blocked_when_remote_not_allowed(self):
        config = make_config(remote=True)
        result = run_diagram_visual_review(
            config,
            self.image_path,
            urlopen=lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("must not be called")),
            allow_remote=False,
        )
        self.assertEqual(result["status"], "blocked")
        self.assertIn("disabled", result["reason"])

    def test_review_failed_when_image_missing(self):
        result = run_diagram_visual_review(
            self.config,
            "/nonexistent/diagram.png",
            urlopen=lambda req, timeout=None: (_ for _ in ()).throw(AssertionError("must not be called")),
            allow_remote=False,
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("missing", result["reason"])

    def test_prompt_restricted_to_readability_concerns(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(make_response(json.dumps(make_review_output())))

        run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        prompt = captured["body"]["messages"][0]["content"][0]["text"]
        for keyword in ("可读性", "遮挡", "层次", "辅助性", "避碰", "箭头", "图例"):
            self.assertIn(keyword, prompt)
        self.assertIn("禁止输出物理结论", prompt)
        self.assertIn("禁止评价或批准答案质量", prompt)
        self.assertIn("suggestions", prompt)

    def test_extra_model_fields_are_not_propagated(self):
        # Model tries to smuggle approval/physics fields: they must be dropped.
        output = make_review_output({
            "status": "approved",
            "physics_conclusion": "答案正确",
            "approved_by": "mimo",
        })

        def fake_urlopen(req, timeout=None):
            return FakeResponse(make_response(json.dumps(output)))

        result = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            set(result.keys()),
            {
                "schema",
                "status",
                "suggestions",
                "model_id",
                "upstream_model",
                "checked_at",
                "reason",
            },
        )
        self.assertEqual(result["suggestions"], make_suggestions())

    def test_output_never_leaks_api_key_or_data_url(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse(make_response(json.dumps(make_review_output())))

        result = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=fake_urlopen,
            allow_remote=False,
        )
        serialized = json.dumps(result)
        self.assertNotIn("secret-key", serialized)
        self.assertNotIn("data:", serialized)
        self.assertNotIn("api_key", captured["body"])

        # Failure paths also keep secrets out of the result
        def bad_urlopen(req, timeout=None):
            raise HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

        failed = run_diagram_visual_review(
            self.config,
            self.image_path,
            urlopen=bad_urlopen,
            allow_remote=False,
        )
        self.assertNotIn("secret-key", json.dumps(failed))


if __name__ == "__main__":
    unittest.main()
