#!/usr/bin/env python3
"""Deterministic mock OpenAI-compatible endpoint that always truncates.

Serves ``POST /v1/chat/completions`` with ``finish_reason=length``,
``message.content=""`` and ``message.reasoning_content="x"*500`` plus
``usage.completion_tokens=6000`` — the reasoning-only truncation shape fixed
in the analysis-run observability work-tree (docs/analysis-run-observability-
w3-pipeline-work-tree.md sections 2/3, A1 fixture). The registry-routed
openai-compatible adapter raises ``_AdapterFailure("output_truncated")`` on
this response and emits the redacted failure envelope, so the
``analysis-core-truncated`` scenario exercises the full classification and
usage-preservation path without any real model or credential.

The mock is started by run_e2e.py (like visual_mock_server) because the model
must be registered in the temp library's model registry with a *passed* probe
before the node script runs.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REASONING_CHARS = 500
COMPLETION_TOKENS = 6000


class _TruncationHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            # Fixed responses; the request body (prompt JSON) is unused.
            self.rfile.read(length)
        body = json.dumps(
            {
                "id": "chatcmpl-e2e-truncated",
                "object": "chat.completion",
                "created": 0,
                "model": "e2e-truncation-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "reasoning_content": "x" * REASONING_CHARS,
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": COMPLETION_TOKENS,
                    "total_tokens": 100 + COMPLETION_TOKENS,
                },
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


def serve_truncation_mock() -> tuple[ThreadingHTTPServer, str]:
    """Start a mock truncation endpoint on a loopback port.

    Returns ``(httpd, base_url)`` where ``base_url`` ends with ``/v1`` so the
    openai-compatible adapter calls ``<base_url>/chat/completions``. The caller
    owns ``httpd.shutdown()`` / ``httpd.server_close()``.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TruncationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"
