"""The job loop: take batches from the cs-redis queue, run them, record C7 worker metrics.

Job types (C7 `type` label): ``fulfil_order`` and ``send_receipt`` come from the
queue (cs-api checkout); ``refresh_catalog`` is scheduled by the worker itself.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol
from uuid import UUID

from prometheus_client import Counter, Histogram

from common.identity import ConfigError, Identity
from common.metrics import WORKER_JOB_BUCKETS, Telemetry

log = logging.getLogger("shop.jobs")

REFRESH_CATALOG_SECONDS = 30.0
RETRY_SECONDS = 1.0
MAX_BATCH_SIZE = 100


class DependencyUnavailable(Exception):
    """cs-redis or cs-postgres could not serve the worker."""


class JobFailed(Exception):
    """A job could not be completed."""


class JobSource(Protocol):
    async def pop_batch(self, max_jobs: int) -> list[Mapping[str, Any]]:
        """Up to ``max_jobs`` jobs; waits at most about a second when the queue is empty."""
        ...

    async def set_catalog(self, products: Sequence[Mapping[str, Any]]) -> None: ...
    async def close(self) -> None: ...


class WorkerStore(Protocol):
    async def mark_fulfilled(self, order_id: UUID) -> bool: ...
    async def order_total(self, order_id: UUID) -> int | None: ...
    async def products(self) -> list[dict[str, Any]]: ...
    async def close(self) -> None: ...


def parse_batch_size(raw: str | None) -> int:
    """``QUEUE_BATCH_SIZE``: an integer from 1 to 100 (default 10)."""
    if raw is None or raw.strip() == "":
        return 10
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"QUEUE_BATCH_SIZE must be an integer, got {raw!r}") from exc
    if not 1 <= value <= MAX_BATCH_SIZE:
        raise ConfigError(f"QUEUE_BATCH_SIZE must be between 1 and {MAX_BATCH_SIZE}, got {value}")
    return value


class WorkerMetrics:
    def __init__(self, identity: Identity, *, with_process: bool = True) -> None:
        self.telemetry = Telemetry(identity, with_process=with_process)
        self.jobs = Counter(
            "worker_jobs_total",
            "Finished background jobs",
            ["type", "result"],
            registry=self.telemetry.registry,
        )
        self.duration = Histogram(
            "worker_job_duration_seconds",
            "Background job duration",
            ["type"],
            buckets=WORKER_JOB_BUCKETS,
            registry=self.telemetry.registry,
        )

    def render(self) -> bytes:
        return self.telemetry.render()


class JobRunner:
    def __init__(
        self,
        source: JobSource,
        store: WorkerStore,
        metrics: WorkerMetrics,
        *,
        batch_size: int,
        receipt_seconds: float = 0.02,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._source = source
        self._store = store
        self._metrics = metrics
        self._batch_size = batch_size
        self._receipt_seconds = receipt_seconds
        self._clock = clock
        self._sleep = sleep
        self._next_refresh = 0.0
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        log.info("job loop started (batch size %d)", self._batch_size)
        while not self._stopping.is_set():
            await self.run_once()

    async def run_once(self) -> None:
        if self._clock() >= self._next_refresh:
            self._next_refresh = self._clock() + REFRESH_CATALOG_SECONDS
            await self.run_job({"type": "refresh_catalog"})
        try:
            batch = await self._source.pop_batch(self._batch_size)
        except DependencyUnavailable:
            log.error("could not read the job queue", exc_info=True)
            await self._sleep(RETRY_SECONDS)
            return
        for job in batch:
            await self.run_job(job)

    async def run_job(self, job: Mapping[str, Any]) -> None:
        job_type = job.get("type")
        handler = {
            "fulfil_order": self._fulfil_order,
            "send_receipt": self._send_receipt,
            "refresh_catalog": self._refresh_catalog,
        }.get(job_type if isinstance(job_type, str) else "")
        if handler is None:
            log.warning("skipped a job of unknown type %r", job_type)
            return
        assert isinstance(job_type, str)
        started = time.perf_counter()
        try:
            await handler(job)
        except (JobFailed, DependencyUnavailable):
            result = "error"
            log.error("job %s failed", job_type, exc_info=True)
        else:
            result = "success"
        self._metrics.duration.labels(type=job_type).observe(time.perf_counter() - started)
        self._metrics.jobs.labels(type=job_type, result=result).inc()

    @staticmethod
    def _order_id(job: Mapping[str, Any]) -> UUID:
        try:
            return UUID(str(job["order_id"]))
        except (KeyError, ValueError) as exc:
            raise JobFailed("job has no valid order_id") from exc

    async def _fulfil_order(self, job: Mapping[str, Any]) -> None:
        order_id = self._order_id(job)
        if not await self._store.mark_fulfilled(order_id):
            raise JobFailed(f"order {order_id} not found")

    async def _send_receipt(self, job: Mapping[str, Any]) -> None:
        order_id = self._order_id(job)
        total = await self._store.order_total(order_id)
        if total is None:
            raise JobFailed(f"order {order_id} not found")
        await self._sleep(self._receipt_seconds)  # stands in for the mail provider call

    async def _refresh_catalog(self, _: Mapping[str, Any]) -> None:
        await self._source.set_catalog(await self._store.products())
