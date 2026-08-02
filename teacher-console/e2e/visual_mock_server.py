#!/usr/bin/env python3
"""Deterministic mock OpenAI-compatible vision endpoint for visual E2E (C5.2-C5.4).

Serves fixed ``wuli.visual-facts.v1`` content (clear or blurred) over
``POST /v1/chat/completions`` so the registry-routed visual extraction can
exercise its full success / fail-closed paths without any real model, API
credential, or student data. The response content carries exactly the five
untrusted content fields the extractor accepts; identity and fingerprints are
attached by the trusted runtime (``visual_extraction.extract_visual_facts``).

The mock lives in the scenario harness (started by run_e2e.py per scenario)
instead of inside each ``.e2e.mjs``: the vision model must be registered in
the temp library's model registry with a *passed* probe before the node script
runs, and probe-passed can only be written in-process against the same
registry file. The node scripts receive the endpoint address via
``E2E_VISION_MOCK_URL``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "visual-routing"

CONTENT_FIELDS = (
    "reviewed_text",
    "printed_facts",
    "diagram_facts",
    "handwriting",
    "uncertainties",
)


def fixture_facts(mode: str) -> dict:
    """Return the five content fields for a mode ('clear' | 'blurred')."""
    if mode == "blurred":
        name = "blurred-question.facts.json"
    else:
        name = "clear-question.facts.json"
    raw = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    return {key: raw[key] for key in CONTENT_FIELDS}


def _make_handler(mode: str):
    facts = fixture_facts(mode)

    class VisualMockHandler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                # Fixed responses; the request body (image data URL) is unused.
                self.rfile.read(length)
            content = json.dumps(facts, ensure_ascii=False)
            body = json.dumps(
                {
                    "id": "chatcmpl-e2e-mock",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "mock-vision",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # keep E2E output quiet
            pass

    return VisualMockHandler


def serve_visual_mock(mode: str) -> tuple[ThreadingHTTPServer, str]:
    """Start a mock vision endpoint on a loopback port.

    Returns ``(httpd, base_url)`` where ``base_url`` ends with ``/v1`` so the
    extractor calls ``<base_url>/chat/completions``. The caller owns
    ``httpd.shutdown()`` / ``httpd.server_close()``.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(mode))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"
