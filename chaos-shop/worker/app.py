"""cs-worker HTTP side (port 8001): /healthz, /metrics and /internal/chaos/leak.

The job loop and the result-retention loop run alongside the HTTP server.
``/internal/*`` needs ``CHAOS_TOKEN`` (chaos CLI only) and is never logged.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from pydantic import BaseModel, ConfigDict

from common.auth import bearer_guard
from common.identity import Identity
from common.metrics import CONTENT_TYPE
from worker.jobs import JobRunner, JobSource, WorkerMetrics, WorkerStore
from worker.retention import ResultRetention

log = logging.getLogger("shop.worker")


class SwitchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def create_app(
    identity: Identity,
    runner: JobRunner,
    metrics: WorkerMetrics,
    source: JobSource,
    store: WorkerStore,
    *,
    retention: ResultRetention,
    chaos_token: str,
) -> FastAPI:
    chaos = Depends(bearer_guard(chaos_token))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks = [
            asyncio.create_task(runner.run(), name="job-loop"),
            asyncio.create_task(retention.run(), name="result-retention"),
        ]
        try:
            yield
        finally:
            # Graceful stop (SIGTERM): clear retention first, before anything can fail.
            retention.on_shutdown()
            runner.stop()
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await source.close()
            await store.close()
            log.info("job loop stopped")

    app = FastAPI(
        title="Chaos Shop worker",
        version=identity.release,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": identity.service, "release": identity.release}

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        return Response(metrics.render(), media_type=CONTENT_TYPE)

    @app.get("/internal/chaos/leak", dependencies=[chaos])
    async def get_leak() -> dict[str, bool | int]:
        return {"enabled": retention.enabled, "held_bytes": retention.held_bytes}

    @app.put("/internal/chaos/leak", dependencies=[chaos])
    async def set_leak(body: SwitchIn) -> dict[str, bool | int]:
        if body.enabled:
            retention.enable()
        else:
            retention.disable()
        return {"enabled": retention.enabled, "held_bytes": retention.held_bytes}

    return app
