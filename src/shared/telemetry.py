"""Structured run diagnostics with task-local logging context."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_context: ContextVar[dict[str, str | int]] = ContextVar("run_log_context", default={})


@contextmanager
def log_context(**fields: str | int) -> Iterator[None]:
    reset = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(reset)


def log_event(logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: object) -> None:
    if logger.isEnabledFor(level):
        logger.log(level, "%s", json.dumps({"event": event, **_context.get(), **fields}))


def error_details(exc: Exception) -> dict[str, str | int]:
    details: dict[str, str | int] = {"error_type": type(exc).__name__}
    frame = exc.__traceback__
    if frame is not None:
        while frame.tb_next is not None:
            frame = frame.tb_next
        details.update(
            error_module=frame.tb_frame.f_globals.get("__name__", "<unknown>"),
            error_function=frame.tb_frame.f_code.co_name,
            error_line=frame.tb_lineno,
        )
    return details
