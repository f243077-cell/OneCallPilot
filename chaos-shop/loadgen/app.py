"""cs-loadgen control API (port 8003). Only the chaos CLI calls it (``CHAOS_TOKEN``).

GET /healthz · GET /internal/status · PUT /internal/mode {"mode": "baseline" | "spike"}
· PUT /internal/coupon-stream {"enabled": bool} (scenario 8).
Mode and coupon stream survive a restart of cs-loadgen (ADR-19); chaos reset clears them.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict

from common.auth import bearer_guard
from common.identity import Identity
from common.state import load_state, save_state, state_dir
from loadgen.traffic import Mode, TrafficGenerator

log = logging.getLogger("chaosshop.loadgen")


class ModeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Mode


class SwitchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def create_app(
    identity: Identity,
    generator: TrafficGenerator,
    client: httpx.AsyncClient,
    *,
    chaos_token: str,
    start_traffic: bool = True,
    state_file: Path | None = None,
) -> FastAPI:
    chaos = Depends(bearer_guard(chaos_token))
    state_path = state_file or state_dir() / "loadgen.json"
    saved = load_state(state_path)
    if saved.get("mode") == "spike":
        generator.schedule.set_mode("spike", time.monotonic())
    generator.set_coupon_stream(saved.get("coupon_stream") is True)

    def persist() -> None:
        save_state(
            state_path,
            {"mode": generator.schedule.mode, "coupon_stream": generator.coupon_stream},
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(generator.run(), name="traffic") if start_traffic else None
        try:
            yield
        finally:
            generator.stop()
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await client.aclose()

    app = FastAPI(
        title="Load generator",
        version=identity.release,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def status() -> dict[str, Any]:
        schedule = generator.schedule
        c = generator.counters
        return {
            "mode": schedule.mode,
            "coupon_stream": generator.coupon_stream,
            "rate_rps": round(schedule.rate(time.monotonic()), 2),
            "baseline_rps": schedule.baseline_rps,
            "spike_rps": schedule.spike_rps,
            "ramp_seconds": schedule.ramp_seconds,
            "sent": c.sent,
            "ok": c.ok,
            "errors": c.errors,
            "dropped": c.dropped,
            "by_status": dict(c.by_status),
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": identity.service, "release": identity.release}

    @app.get("/internal/status", dependencies=[chaos])
    async def get_status() -> dict[str, Any]:
        return status()

    @app.put("/internal/mode", dependencies=[chaos])
    async def set_mode(body: ModeIn) -> dict[str, Any]:
        generator.schedule.set_mode(body.mode, time.monotonic())
        persist()
        log.info("traffic mode set to %s", body.mode)
        return status()

    @app.put("/internal/coupon-stream", dependencies=[chaos])
    async def set_coupon_stream(body: SwitchIn) -> dict[str, Any]:
        generator.set_coupon_stream(body.enabled)
        persist()
        log.info("coupon stream %s", "on" if body.enabled else "off")
        return status()

    return app
