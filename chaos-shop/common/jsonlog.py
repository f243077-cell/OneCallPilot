"""C7 structured logs: one JSON object per line on stdout (TB-004, contracts/telemetry.md §3).

Fields: ``ts``, ``level``, ``service``, ``release``, ``instance``, ``logger``, ``msg``;
request lines add ``request_id``, ``route``, ``status``, ``duration_ms``; exception
lines add ``exc_type``, ``exc_message``, ``stacktrace``. Keys that do not apply are
omitted. Never log environment values, passwords, tokens or Authorization headers.
"""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from common.identity import Identity

MAX_LINE_BYTES = 16 * 1024 - 1  # 16 KiB including the trailing newline
TRUNCATED = "…[truncated]"
TRACEBACK_HEAD = "Traceback (most recent call last):"
LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Set per request by the api middleware, so every line logged while handling
# the request carries its id and route without passing them around.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
route_var: ContextVar[str | None] = ContextVar("route", default=None)

_REQUEST_KEYS = ("request_id", "route", "status", "duration_ms")


def format_ts(created: float) -> str:
    moment = datetime.fromtimestamp(created, UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def _level(record: logging.LogRecord) -> str:
    name = record.levelname.upper()
    if name in LEVELS:
        return name
    if record.levelno >= logging.CRITICAL:
        return "CRITICAL"
    if record.levelno >= logging.ERROR:
        return "ERROR"
    if record.levelno >= logging.WARNING:
        return "WARNING"
    return "INFO" if record.levelno >= logging.INFO else "DEBUG"


def _dumps(fields: dict[str, Any]) -> str:
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":"), default=str)


# (field, characters always kept): first trim each free-text field down to 1,024
# characters, then, only if still needed, to 64 (enough for the Traceback head).
_TRUNCATION_STEPS = (
    ("exc_message", 1024),
    ("stacktrace", 1024),
    ("msg", 1024),
    ("exc_message", 64),
    ("stacktrace", 64),
    ("msg", 64),
)
_SUFFIX_BYTES = len(TRUNCATED.encode("utf-8"))


def _fit(fields: dict[str, Any]) -> str:
    """Truncate the long free-text fields until the line fits in 16 KiB."""
    line = _dumps(fields)
    for key, floor in _TRUNCATION_STEPS:
        excess = len(line.encode("utf-8")) - MAX_LINE_BYTES
        if excess <= 0:
            break
        value = fields.get(key)
        if not isinstance(value, str) or len(value) <= floor + len(TRUNCATED):
            continue
        # Removing n characters removes at least n bytes from the JSON text.
        keep = max(floor, len(value) - excess - _SUFFIX_BYTES)
        fields[key] = value[:keep] + TRUNCATED
        line = _dumps(fields)
    return line


class JsonFormatter(logging.Formatter):
    def __init__(self, identity: Identity) -> None:
        super().__init__()
        self._identity = identity

    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            "ts": format_ts(record.created),
            "level": _level(record),
            "service": self._identity.service,
            "release": self._identity.release,
            "instance": self._identity.instance,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in _REQUEST_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                fields[key] = value
        if "request_id" not in fields and (request_id := request_id_var.get()) is not None:
            fields["request_id"] = request_id
        if "route" not in fields and (route := route_var.get()) is not None:
            fields["route"] = route
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                fields.setdefault(str(key), value)
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            fields["exc_type"] = type(exc).__name__
            fields["exc_message"] = str(exc)
            stacktrace = "".join(traceback.format_exception(exc)).rstrip("\n")
            if not stacktrace.startswith(TRACEBACK_HEAD):
                stacktrace = f"{TRACEBACK_HEAD}\n{stacktrace}"
            fields["stacktrace"] = stacktrace
        return _fit(fields)


def configure_logging(identity: Identity, level: int = logging.INFO) -> None:
    """Send every logger (including uvicorn's) through the JSON formatter on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(identity))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "httpcore"):
        child = logging.getLogger(name)
        child.handlers[:] = []
        child.propagate = True
    # The api writes its own access line (C7); uvicorn's would be plain text.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx logs every outgoing request at INFO; keep only its warnings.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
