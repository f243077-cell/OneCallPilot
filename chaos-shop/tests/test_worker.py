"""cs-worker job loop (TB-001)."""

import asyncio
import json
from collections.abc import Iterator
from uuid import uuid4

import pytest

from common.identity import ConfigError, Identity
from tests.conftest import ListHandler, capture
from tests.contract import log_problems
from tests.fakes import FakeJobSource, FakeWorkerStore
from worker.jobs import JobRunner, WorkerMetrics, parse_batch_size

IDENTITY = Identity("worker", "2.1.0", "cs-worker-210", "69bbb70d749c")


@pytest.fixture
def logs() -> Iterator[ListHandler]:
    yield from capture(IDENTITY)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_runner(
    source: FakeJobSource, store: FakeWorkerStore, clock: Clock, batch_size: int = 10
) -> tuple[JobRunner, WorkerMetrics]:
    metrics = WorkerMetrics(IDENTITY, with_process=False)
    runner = JobRunner(
        source, store, metrics, batch_size=batch_size, clock=clock, sleep=clock.sleep
    )
    return runner, metrics


def count(metrics: WorkerMetrics, job_type: str, result: str) -> float:
    value = metrics.telemetry.registry.get_sample_value(
        "worker_jobs_total", {"type": job_type, "result": result}
    )
    return value or 0.0


@pytest.mark.parametrize("raw,expected", [(None, 10), ("", 10), ("1", 1), ("100", 100)])
def test_batch_size_accepts_1_to_100(raw: str | None, expected: int) -> None:
    assert parse_batch_size(raw) == expected


@pytest.mark.parametrize("raw", ["0", "101", "-5", "ten", "1.5"])
def test_batch_size_rejects_other_values(raw: str) -> None:
    with pytest.raises(ConfigError):
        parse_batch_size(raw)


def test_jobs_run_and_are_counted(logs: ListHandler) -> None:
    source, store, clock = FakeJobSource(), FakeWorkerStore(), Clock()
    order = uuid4()
    store.orders[order] = "paid"
    store.totals[order] = 2400
    source.queue = [
        {"type": "fulfil_order", "order_id": str(order)},
        {"type": "send_receipt", "order_id": str(order)},
        {"type": "send_receipt", "order_id": str(uuid4())},
        {"type": "unknown_kind"},
    ]
    runner, metrics = make_runner(source, store, clock)
    asyncio.run(runner.run_once())
    assert store.orders[order] == "fulfilled"
    assert source.catalog is not None  # refresh_catalog runs on the first pass
    assert count(metrics, "refresh_catalog", "success") == 1
    assert count(metrics, "fulfil_order", "success") == 1
    assert count(metrics, "send_receipt", "success") == 1
    assert count(metrics, "send_receipt", "error") == 1
    records = [json.loads(line) for line in logs.lines]
    assert any(r.get("exc_type") == "JobFailed" for r in records)
    assert all(log_problems(line) == [] for line in logs.lines)


def test_batches_respect_the_batch_size() -> None:
    source, store, clock = FakeJobSource(), FakeWorkerStore(), Clock()
    source.queue = [{"type": "fulfil_order", "order_id": str(uuid4())} for _ in range(5)]
    runner, _ = make_runner(source, store, clock, batch_size=2)
    asyncio.run(runner.run_once())
    assert len(source.queue) == 3


def test_catalog_refresh_runs_every_30_seconds() -> None:
    source, store, clock = FakeJobSource(), FakeWorkerStore(), Clock()
    runner, metrics = make_runner(source, store, clock)

    async def passes() -> None:
        await runner.run_once()
        clock.now += 10
        await runner.run_once()
        clock.now += 25
        await runner.run_once()

    asyncio.run(passes())
    assert count(metrics, "refresh_catalog", "success") == 2


def test_queue_outage_is_logged_and_retried(logs: ListHandler) -> None:
    source, store, clock = FakeJobSource(), FakeWorkerStore(), Clock()
    source.down = True
    runner, metrics = make_runner(source, store, clock)
    asyncio.run(runner.run_once())
    assert clock.slept == [1.0]
    assert count(metrics, "refresh_catalog", "error") == 1
    records = [json.loads(line) for line in logs.lines]
    assert [r["level"] for r in records if "stacktrace" in r] == ["ERROR", "ERROR"]
