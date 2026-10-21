"""JSON logs to stdout with the standard library (``CLAUDE.md`` §4.3).

Callers pass identifiers through ``extra=``; only the keys in ``CONTEXT_KEYS``
are copied into the record, so a request body or a token can never ride along.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

CONTEXT_KEYS = ("incident_id", "proposal_id", "execution_id", "request_id", "user_id", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in CONTEXT_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = str(value)
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc_type"] = record.exc_info[0].__name__
        return json.dumps(entry)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs every request (URL and query) at INFO: the detector alone makes
    # about 20 a cycle. Keep their warnings, not the per-request lines.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(max(level, logging.WARNING))
