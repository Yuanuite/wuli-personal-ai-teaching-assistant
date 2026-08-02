#!/usr/bin/env python3
"""Deterministic mock OpenAI-compatible endpoint that hangs past the soft deadline.

Serves ``POST /v1/chat/completions`` by sleeping longer than the adapter's HTTP
soft deadline before answering, so the registry-routed openai-compatible
adapter times out on its own and the Gateway records a single
``provider_timeout`` attempt together with the frozen three-layer deadline
budget (work-tree A2.1/A2.4, scenario ``analysis-soft-timeout``). The late
response is never consumed: by the time the handler wakes up, the adapter has
already closed the connection.

The mock is started by run_e2e.py (like truncation_mock_server) because the
model must be registered in the temp library's model registry with a *passed*
probe and a *qualified* analysis record before the node script runs.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Longer than the adapter's HTTP soft deadline (derived from the model's
# timeout_seconds, see run_e2e.configure_slow_test_model), so the adapter —
# not the Gateway subprocess kill — must be the first to time out.
SLEEP_SECONDS = 15.0


class _SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        time.sleep(SLEEP_SECONDS)
        try:
            body = json.dumps(
                {
                    "id": "chatcmpl-e2e-slow",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "e2e-slow-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "{}"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            # The adapter gave up at its HTTP soft deadline and closed the
            # connection; a late write must not surface as a test failure.
            pass

    def log_message(self, *args):  # keep E2E output quiet
        pass


def serve_slow_mock() -> tuple[ThreadingHTTPServer, str]:
    """Start a mock hanging endpoint on a loopback port.

    Returns ``(httpd, base_url)`` where ``base_url`` ends with ``/v1`` so the
    openai-compatible adapter calls ``<base_url>/chat/completions``. The caller
    owns ``httpd.shutdown()`` / ``httpd.server_close()``.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"
