"""Shared logging setup with pipeline trace_id for the teacher console.

Usage
-----
    from log import logger, TraceContext

    # At a pipeline entry point:
    with TraceContext("entry-abc-123") as ctx:
        ctx.info("stage=analysis status=started")
        ...
        ctx.info("stage=analysis entry_id=%s status=%s", entry_id, "completed")
        ctx.warning("stage=analysis retry_count=%d", retries)

Outside a context, plain ``logger.info(...)`` works but carries no trace_id.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

_TRACE_ID_KEY = "pipeline_trace_id"


def thread_id() -> int:
    """Return the current thread ID (portable across Python versions)."""
    import threading

    return threading.current_thread().ident or 0


def _set_trace_id(tid: int, value: str | None) -> None:
    mapping = getattr(logging, _TRACE_ID_KEY, {})
    if value is None:
        mapping.pop(tid, None)
    else:
        mapping[tid] = value
    setattr(logging, _TRACE_ID_KEY, mapping)


class _TraceFilter(logging.Filter):
    """Filter that injects the current thread's trace_id into every log record.

    This runs before any formatter, so *all* handlers (even those using a plain
    ``logging.Formatter``) see ``record.trace_id``.  Install with
    ``addFilter(_TraceFilter())`` on the logger or handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = getattr(logging, _TRACE_ID_KEY, {}).get(thread_id(), None)
        record.trace_id = trace_id or "-"
        return True


def configure(*, level: int = logging.INFO, log_file: str | None = None) -> None:
    """Configure the root logger once at process start.

    Parameters
    ----------
    level : int
        Logging threshold (default ``logging.INFO``).
    log_file : str or None
        If set, also write to this file (rotated on process restart).
    """
    logger = logging.getLogger("wuli")
    # The same stream handler is also attached to root for third-party modules.
    # Stop wuli records from propagating there, otherwise every pipeline event
    # is emitted twice.
    logger.propagate = False
    if logger.handlers:
        return  # already configured

    logger.setLevel(level)
    logger.addFilter(_TraceFilter())
    fmt = "%(asctime)s [%(levelname)s] [%(trace_id)s] %(message)s"

    handler: logging.Handler
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        logger.addHandler(fh)

    # Also configure the root logger so imported modules see the same trace_id
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
        root.addFilter(_TraceFilter())
    root.setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a named child of the ``wuli`` logger."""
    return logging.getLogger("wuli" + (f".{name}" if name else ""))


logger = get_logger()


@contextmanager
def TraceContext(trace_id: str | None = None, *, log: logging.Logger | None = None) -> Iterator[logging.Logger]:
    """Context manager that binds a trace_id to the current thread for all log output.

    Parameters
    ----------
    trace_id : str or None
        Explicit identifier.  Auto-generated as a short UUID when omitted.
    log : logging.Logger or None
        Logger to yield (defaults to the module-level ``logger``).

    Example
    -------
        with TraceContext() as ctx:
            ctx.info("pipeline started")   # → [a1b2c3d4] pipeline started
    """
    tid = thread_id()
    tid_label = trace_id or uuid.uuid4().hex[:12]
    _set_trace_id(tid, tid_label)
    target = log or logger
    try:
        yield target
    finally:
        _set_trace_id(tid, None)
