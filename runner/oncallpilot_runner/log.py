"""JSON logs on stdout (CLAUDE.md §4.3). Never log keys, env values or message bodies:
callers pass only ids, the action, codes and reasons as `extra` fields."""

import json
import logging
import sys
from datetime import UTC, datetime

FIELDS = ("request_id", "execution_id", "action", "error_code", "entry_id", "decision")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "service": "runner",
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                line[field] = value
        if record.exc_info and record.exc_info[0] is not None:
            line["exc_type"] = record.exc_info[0].__name__
            line["stacktrace"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False)


def configure(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
