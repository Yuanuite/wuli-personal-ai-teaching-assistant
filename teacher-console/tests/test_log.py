"""Unit tests for teacher-console logging module (log.py)."""

import io
import logging
import re
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CONSOLE = ROOT / "teacher-console"
import sys  # noqa: E402

sys.path.insert(0, str(CONSOLE))

from log import TraceContext, _TraceFilter, configure, get_logger, logger  # noqa: E402


class LogTest(unittest.TestCase):
    def setUp(self):
        # Capture log output by adding a stream handler to the test logger.
        # Set level explicitly: the "wuli" logger has NOTSET by default,
        # so it inherits root WARNING — info wouldn't be emitted.
        logger.setLevel(logging.INFO)
        self.buf = io.StringIO()
        handler = logging.StreamHandler(self.buf)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(levelname)s [%(trace_id)s] %(message)s"))
        handler.addFilter(_TraceFilter())
        self.handler = handler
        logger.addHandler(handler)

    def tearDown(self):
        logger.removeHandler(self.handler)

    def test_configure_idempotent(self):
        """Calling configure() twice does not duplicate handlers."""
        configure()
        count_before = len(logger.handlers)
        configure()
        count_after = len(logger.handlers)
        self.assertEqual(count_before, count_after)

    def test_trace_context_injects_trace_id(self):
        """Messages inside TraceContext carry a trace_id."""
        with TraceContext("my-test-trace"):
            logger.info("hello world")
        output = self.buf.getvalue()
        self.assertIn("my-test-trace", output)
        self.assertIn("hello world", output)

    def test_trace_context_auto_generates_id(self):
        """TraceContext generates a short UUID when no trace_id is given."""
        with TraceContext():
            logger.info("auto-id")
        output = self.buf.getvalue()
        match = re.search(r"\[([a-f0-9]{12})\]", output)
        self.assertIsNotNone(match, f"no 12-char hex trace_id found in: {output}")
        self.assertIn("auto-id", output)

    def test_trace_context_clears_after_exit(self):
        """trace_id resets to '-' after TraceContext exits."""
        with TraceContext("inside"):
            logger.info("in context")
        logger.info("outside context")
        output = self.buf.getvalue()
        self.assertIn("[inside]", output)
        # Outside context the trace_id should be "-"
        outside_lines = [line for line in output.splitlines() if "outside" in line]
        self.assertTrue(
            any("[-]" in line for line in outside_lines), f"expected trace_id '-' in outside line, got: {outside_lines}"
        )

    def test_trace_context_per_thread_isolation(self):
        """Two threads with different TraceContexts use different trace_ids."""
        seen = []

        def worker(label: str):
            with TraceContext(label):
                logger.info("thread %s", label)
                # Snapshot only lines that contain our label.
                for line in self.buf.getvalue().splitlines():
                    if label in line:
                        seen.append(line)

        t1 = threading.Thread(target=worker, args=("A",))
        t2 = threading.Thread(target=worker, args=("B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        a_lines = [line for line in seen if "thread A" in line]
        b_lines = [line for line in seen if "thread B" in line]
        self.assertTrue(any("[A]" in line for line in a_lines), f"no [A] in A-lines: {a_lines}")
        self.assertTrue(any("[B]" in line for line in b_lines), f"no [B] in B-lines: {b_lines}")

    def test_get_logger_returns_named_child(self):
        child = get_logger("test.child")
        self.assertEqual(child.name, "wuli.test.child")

    def test_get_logger_returns_root_when_no_name(self):
        root = get_logger()
        self.assertEqual(root.name, "wuli")


if __name__ == "__main__":
    unittest.main()
