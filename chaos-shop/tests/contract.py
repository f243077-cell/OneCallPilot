"""The C7 telemetry contract as data (contracts/telemetry.md), shared by the tests.

If the contract changes, change it here in the same contract PR.
"""

import json
import re
from typing import Any

from prometheus_client.parser import text_string_to_metric_families

BASE_LABELS = frozenset({"service", "release", "instance"})

# metric family name -> (type, extra labels)
API_METRICS: dict[str, tuple[str, frozenset[str]]] = {
    "http_requests": ("counter", frozenset({"route", "method", "status"})),
    "http_request_duration_seconds": ("histogram", frozenset({"route", "method"})),
    "db_pool_connections": ("gauge", frozenset({"state"})),
    "db_pool_wait_seconds": ("histogram", frozenset()),
    "db_pool_timeouts": ("counter", frozenset()),
    "cache_operations": ("counter", frozenset({"cache", "result"})),
    "upstream_request_duration_seconds": ("histogram", frozenset({"upstream"})),
    "app_info": ("gauge", frozenset({"commit"})),
}
WORKER_METRICS: dict[str, tuple[str, frozenset[str]]] = {
    "worker_jobs": ("counter", frozenset({"type", "result"})),
    "worker_job_duration_seconds": ("histogram", frozenset({"type"})),
    "app_info": ("gauge", frozenset({"commit"})),
}
PROCESS_METRICS = (
    "process_resident_memory_bytes",
    "process_cpu_seconds",
    "process_start_time_seconds",
)

LABEL_VALUES: dict[str, frozenset[str]] = {
    "route": frozenset({"/products", "/cart", "/checkout", "/orders", "/healthz", "other"}),
    "method": frozenset({"GET", "POST", "PUT", "DELETE", "OTHER"}),
    "state": frozenset({"in_use", "idle"}),
    "cache": frozenset({"catalog", "pricing"}),
    "upstream": frozenset({"payments"}),
    "type": frozenset({"fulfil_order", "send_receipt", "refresh_catalog"}),
}
RESULT_VALUES = {
    "cache_operations": frozenset({"hit", "miss", "error"}),
    "worker_jobs": frozenset({"success", "error"}),
}

BUCKETS: dict[str, tuple[float, ...]] = {
    "http_request_duration_seconds": (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
    "upstream_request_duration_seconds": (
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30,
    ),
    "db_pool_wait_seconds": (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 2.5, 5, 10, 30),
    "worker_job_duration_seconds": (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
}  # fmt: skip


def metric_problems(
    text: str,
    expected: dict[str, tuple[str, frozenset[str]]],
    base: dict[str, str],
    *,
    require_process: bool,
) -> list[str]:
    """Every contract problem in a /metrics body; an empty list means it conforms."""
    problems: list[str] = []
    families = {f.name: f for f in text_string_to_metric_families(text)}
    wanted = dict(expected)
    if require_process:
        for name in PROCESS_METRICS:
            wanted[name] = ("", frozenset())
    for name, (kind, extra) in wanted.items():
        family = families.get(name)
        if family is None:
            problems.append(f"missing metric {name}")
            continue
        if kind and family.type != kind:
            problems.append(f"{name} has type {family.type}, expected {kind}")
        if not family.samples:
            problems.append(f"{name} has no samples")
        for sample in family.samples:
            labels = set(sample.labels) - {"le"}
            if labels != BASE_LABELS | extra:
                problems.append(f"{sample.name} has labels {sorted(labels)}")
            for key, value in base.items():
                if sample.labels.get(key) != value:
                    problems.append(f"{sample.name} has {key}={sample.labels.get(key)!r}")
            for key, allowed in LABEL_VALUES.items():
                if key in sample.labels and sample.labels[key] not in allowed:
                    problems.append(f"{sample.name} has {key}={sample.labels[key]!r}")
            if "result" in sample.labels and sample.labels["result"] not in RESULT_VALUES[name]:
                problems.append(f"{sample.name} has result={sample.labels['result']!r}")
            if "status" in sample.labels and not re.fullmatch(
                r"[1-5][0-9]{2}", sample.labels["status"]
            ):
                problems.append(f"{sample.name} has status={sample.labels['status']!r}")
        if name in BUCKETS:
            seen = sorted({float(s.labels["le"]) for s in family.samples if "le" in s.labels})
            if tuple(seen) != tuple(sorted(BUCKETS[name])) + (float("inf"),):
                problems.append(f"{name} has buckets {seen}")
    for family in families.values():
        for sample in family.samples:
            if not BASE_LABELS <= set(sample.labels):
                problems.append(f"{sample.name} lacks the base labels")
    return problems


# --- Logs (contracts/telemetry.md §3) ---

LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
ALWAYS: dict[str, type] = {
    "ts": str,
    "level": str,
    "service": str,
    "release": str,
    "instance": str,
    "logger": str,
    "msg": str,
}
EXCEPTION_KEYS = ("exc_type", "exc_message", "stacktrace")
MAX_LINE_BYTES = 16 * 1024


def log_problems(line: str, base: dict[str, str] | None = None) -> list[str]:
    """Every contract problem in one log line; an empty list means it conforms.

    Request lines (lines with ``status``) need ``request_id``, ``status`` and
    ``duration_ms``, and ``route`` from the C7 route values, except on
    cs-payments, whose request lines carry no ``route``.
    """
    problems: list[str] = []
    if len(line.encode("utf-8")) + 1 > MAX_LINE_BYTES:
        problems.append("line longer than 16 KiB")
    if "\n" in line:
        problems.append("line contains a raw newline")
    try:
        record: Any = json.loads(line)
    except ValueError:
        return problems + ["not JSON"]
    if not isinstance(record, dict):
        return problems + ["not a JSON object"]
    for key, kind in ALWAYS.items():
        if not isinstance(record.get(key), kind):
            problems.append(f"{key} missing or not a {kind.__name__}")
    for key, value in record.items():
        if value is None:
            problems.append(f"{key} is null (omit it instead)")
    if isinstance(record.get("ts"), str) and not TS.fullmatch(record["ts"]):
        problems.append(f"ts {record['ts']!r} is not RFC 3339 UTC with milliseconds")
    if record.get("level") not in LEVELS:
        problems.append(f"level {record.get('level')!r}")
    for key, value in (base or {}).items():
        if record.get(key) != value:
            problems.append(f"{key}={record.get(key)!r}, expected {value!r}")
    if "status" in record:
        if not isinstance(record["status"], int):
            problems.append("status is not an integer")
        if not isinstance(record.get("duration_ms"), int | float):
            problems.append("duration_ms missing or not a number")
        if not isinstance(record.get("request_id"), str) or not REQUEST_ID.fullmatch(
            record["request_id"]
        ):
            problems.append("request_id missing or not 32 lower-case hex")
        if record.get("service") != "payments" and record.get("route") not in LABEL_VALUES["route"]:
            problems.append(f"route {record.get('route')!r}")
    if "route" in record and record["route"] not in LABEL_VALUES["route"]:
        problems.append(f"route {record['route']!r}")
    present = [key for key in EXCEPTION_KEYS if key in record]
    if present:
        if len(present) != len(EXCEPTION_KEYS):
            problems.append(f"exception line has only {present}")
        if record.get("level") not in ("ERROR", "CRITICAL"):
            problems.append("exception logged below ERROR")
        stacktrace = record.get("stacktrace")
        if not isinstance(stacktrace, str) or not stacktrace.startswith(
            "Traceback (most recent call last):"
        ):
            problems.append("stacktrace does not start with 'Traceback'")
    return problems
