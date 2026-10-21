"""JSON logging setup (CLAUDE.md §4.3): INFO is for state transitions, so the
HTTP clients' per-request lines (every detector cycle, every tool call, with
query strings) stay out of it; their warnings still show."""

import logging

from oncallpilot.jsonlog import configure_logging


def test_http_client_request_lines_are_not_logged_at_info() -> None:
    configure_logging()
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        assert not logger.isEnabledFor(logging.INFO)
        assert logger.isEnabledFor(logging.WARNING)
    assert logging.getLogger("oncallpilot.worker").isEnabledFor(logging.INFO)
