"""``query_logs`` (architecture §6.3; AI-005): Loki through one fixed LogQL template.

Returns a ``LogQueryPayload`` (C1): up to 50 lines of ≤ 200 characters, the
total number of matching lines in the window, and the top 5 exception types.
The content is untrusted and is redacted by the evidence pipeline before it
is stored or shown to the model (``CLAUDE.md`` §5.4).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from oncallpilot_contracts.common import UtcDatetime
from oncallpilot_contracts.domain import ExceptionCount, LogLine, LogQueryPayload
from oncallpilot_contracts.enums import LogLevel
from pydantic import BaseModel, ConfigDict, Field

from oncallpilot.agent.tools.clients import LokiClient
from oncallpilot.agent.tools.logql import (
    MAX_CONTAINS,
    LogLevelFilter,
    LogService,
    count_query,
    exception_types_query,
    selector,
)
from oncallpilot.agent.tools.window import clamp

MAX_LINES = 50
MAX_LINE_CHARS = 200
_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "UNKNOWN"})


class QueryLogsParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: LogService
    level: LogLevelFilter = "ANY"
    contains: str | None = Field(default=None, min_length=1, max_length=MAX_CONTAINS)
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    limit: int = Field(default=MAX_LINES, ge=1, le=MAX_LINES)


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def parse_line(labels: dict[str, str], ns: int, raw: str) -> LogLine:
    level = labels.get("level", "UNKNOWN")
    msg, exc_type = raw, None
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if isinstance(body, dict):
        msg = str(body.get("msg", raw))
        if body.get("exc_type"):
            exc_type = _cut(str(body["exc_type"]), 100)
    checked: LogLevel = level if level in _LEVELS else "UNKNOWN"  # type: ignore[assignment]
    return LogLine(
        ts=datetime.fromtimestamp(ns / 1e9, UTC),
        level=checked,
        msg=_cut(msg, MAX_LINE_CHARS),
        exc_type=exc_type,
    )


async def query_logs(
    loki: LokiClient,
    params: QueryLogsParams,
    incident_start: datetime,
    incident_end: datetime | None,
    now: datetime,
) -> LogQueryPayload:
    start, end = clamp(params.start, params.end, incident_start, incident_end, now)
    stream = selector(params.service, params.level, params.contains)
    seconds = max(1, int((end - start).total_seconds()))
    raw_lines = await loki.query_range(stream, start, end, params.limit)
    counted = await loki.query_vector(count_query(stream, seconds), end)
    types = await loki.query_vector(exception_types_query(stream, seconds), end)
    top = sorted(
        (
            ExceptionCount(exc_type=_cut(labels["exc_type"], 100), count=int(value))
            for labels, value in types
            if labels.get("exc_type") and value >= 1
        ),
        key=lambda item: item.count,
        reverse=True,
    )[:5]
    return LogQueryPayload(
        lines=[parse_line(labels, ns, raw) for labels, ns, raw in raw_lines][: params.limit],
        total_count=int(counted[0][1]) if counted else 0,
        top_exception_types=top,
    )
