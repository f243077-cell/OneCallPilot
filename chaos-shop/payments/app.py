"""cs-payments (port 8002): POST /charge, GET /healthz, GET|PUT /internal/delay.

Every charge takes a small random base latency plus the configured extra delay.
The delay is set only by the chaos CLI (``CHAOS_TOKEN``) and starts at 0.
Payments exposes no metrics (C7 §2.1). Its request lines carry ``request_id``,
``status`` and ``duration_ms`` but no ``route``: C7 route values name cs-api routes.
"""

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from common.auth import bearer_guard
from common.identity import Identity
from common.jsonlog import request_id_var
from common.state import load_state, save_state, state_dir

REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
BASE_LATENCY_SECONDS = (0.02, 0.06)
MAX_DELAY_MS = 10_000

log_access = logging.getLogger("chaosshop.access")
log_admin = logging.getLogger("chaosshop.admin")


class ChargeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(ge=0)


class DelayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delay_ms: int = Field(ge=0, le=MAX_DELAY_MS)


def create_app(
    identity: Identity,
    *,
    chaos_token: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
    state_file: Path | None = None,
) -> FastAPI:
    # The delay survives a restart of cs-payments (ADR-19); chaos reset sets it to 0.
    state_path = state_file or state_dir() / "payments.json"
    saved = load_state(state_path).get("delay_ms", 0)
    state = {"delay_ms": saved if isinstance(saved, int) and 0 <= saved <= MAX_DELAY_MS else 0}
    jitter = rng or random.Random()
    chaos = Depends(bearer_guard(chaos_token))
    app = FastAPI(
        title="Payments",
        version=identity.release,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def access_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith("/internal/"):
            return await call_next(request)
        header = request.headers.get("x-request-id", "")
        request_id = header if REQUEST_ID.fullmatch(header) else uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed = time.perf_counter() - started
            log_access.log(
                logging.ERROR if response.status_code >= 500 else logging.INFO,
                "%s %s %d",
                request.method,
                request.url.path,
                response.status_code,
                extra={
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 1),
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": identity.service, "release": identity.release}

    @app.post("/charge")
    async def charge(body: ChargeIn) -> dict[str, str]:
        await sleep(jitter.uniform(*BASE_LATENCY_SECONDS) + state["delay_ms"] / 1000)
        return {"charge_id": uuid4().hex, "status": "approved"}

    @app.get("/internal/delay", dependencies=[chaos])
    async def get_delay() -> dict[str, int]:
        return {"delay_ms": state["delay_ms"]}

    @app.put("/internal/delay", dependencies=[chaos])
    async def set_delay(body: DelayIn) -> dict[str, int]:
        state["delay_ms"] = body.delay_ms
        save_state(state_path, {"delay_ms": body.delay_ms})
        # DEBUG (off by default): operator changes are not part of the provider's log.
        log_admin.debug("extra delay set to %d ms", body.delay_ms)
        return {"delay_ms": body.delay_ms}

    return app
