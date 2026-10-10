"""Run a service under uvicorn with C7 logging; a crash on start-up is logged as CRITICAL JSON."""

import logging
import sys
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

from common.identity import Identity, load_identity
from common.jsonlog import configure_logging

log = logging.getLogger("shop.main")


def serve(
    service: str, port: int, build: Callable[[Identity], FastAPI], *, with_commit: bool
) -> None:
    # Until the identity is known, a failure can only be reported in plain text.
    try:
        identity = load_identity(service, with_commit=with_commit)
    except Exception as exc:
        print(f"{service}: cannot start: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    configure_logging(identity)
    try:
        app = build(identity)
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=port,
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=10,
        )
    except Exception:
        log.critical("service failed to start", exc_info=True)
        raise SystemExit(1) from None
