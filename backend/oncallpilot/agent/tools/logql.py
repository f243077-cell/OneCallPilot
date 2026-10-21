"""The fixed LogQL template of ``query_logs`` (architecture §6.3; C7 §3.4; AI-005).

``service`` and ``level`` are enums; ``contains`` is a literal that is escaped
into a double-quoted LogQL string and used with ``|=`` (substring, never a
regex), so no value can change the query's structure.
"""

from __future__ import annotations

from typing import Literal

LogService = Literal["api", "worker", "lb", "payments", "redis", "postgres"]
LogLevelFilter = Literal["ERROR", "WARNING", "INFO", "ANY"]

MAX_CONTAINS = 64

# ERROR includes CRITICAL (a crash on start-up is logged at CRITICAL, C7 §3.2).
_LEVEL_MATCHERS: dict[str, str] = {
    "ERROR": ', level=~"ERROR|CRITICAL"',
    "WARNING": ', level="WARNING"',
    "INFO": ', level="INFO"',
    "ANY": "",
}


def escape_literal(value: str) -> str:
    """A LogQL double-quoted string literal that matches ``value`` exactly."""
    out = []
    for char in value:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\x{ord(char):02x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def selector(service: LogService, level: LogLevelFilter, contains: str | None) -> str:
    """The log selector and filter, e.g. ``{service="api", level=~"ERROR|CRITICAL"} |= "x"``."""
    if service not in ("api", "worker", "lb", "payments", "redis", "postgres"):
        raise ValueError(f"unknown service {service!r}")
    if level not in _LEVEL_MATCHERS:
        raise ValueError(f"unknown level {level!r}")
    query = f'{{service="{service}"{_LEVEL_MATCHERS[level]}}}'
    if contains:
        if len(contains) > MAX_CONTAINS:
            raise ValueError(f"contains is longer than {MAX_CONTAINS} characters")
        query += f" |= {escape_literal(contains)}"
    return query


def count_query(stream: str, range_seconds: int) -> str:
    return f"sum(count_over_time({stream} [{range_seconds}s]))"


def exception_types_query(stream: str, range_seconds: int) -> str:
    """Top 5 exception types among the matching JSON lines."""
    return (
        f"topk(5, sum by (exc_type) (count_over_time({stream}"
        f' | json exc_type="exc_type" | exc_type != "" [{range_seconds}s])))'
    )
