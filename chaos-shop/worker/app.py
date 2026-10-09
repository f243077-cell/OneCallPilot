"""cs-worker HTTP side (port 8001): /healthz and /metrics; the job loop runs alongside."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response

from common.identity import Identity
from common.metrics import CONTENT_TYPE
from worker.jobs import JobRunner, JobSource, WorkerMetrics, WorkerStore

log = logging.getLogger("chaosshop.worker")


def create_app(
    identity: Identity,
    runner: JobRunner,
    metrics: WorkerMetrics,
    source: JobSource,
    store: WorkerStore,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(runner.run(), name="job-loop")
        try:
            yield
        finally:
            runner.stop()
            task.cancel()
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

    return app
