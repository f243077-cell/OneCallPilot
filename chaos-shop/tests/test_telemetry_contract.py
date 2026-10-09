"""TB-003: /metrics of cs-api and cs-worker carry every C7 metric with the right labels."""

import sys
from collections.abc import Iterable

from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.metrics_core import Metric
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client.registry import Collector

from common.identity import Identity
from common.metrics import Telemetry
from tests.conftest import ApiHarness
from tests.contract import API_METRICS, WORKER_METRICS, metric_problems
from tests.fakes import FakeJobSource, FakeWorkerStore
from worker.app import create_app as create_worker_app
from worker.jobs import JobRunner, WorkerMetrics

ON_LINUX = sys.platform == "linux"  # the process collector reads /proc
API_BASE = {"service": "api", "release": "1.4.0", "instance": "cs-api-140-1"}
WORKER_IDENTITY = Identity("worker", "2.1.0", "cs-worker-210", "69bbb70d749c")
WORKER_BASE = {"service": "worker", "release": "2.1.0", "instance": "cs-worker-210"}


def drive_api_traffic(api: ApiHarness) -> None:
    api.client.get("/healthz")
    api.client.get("/products")  # catalog miss
    api.client.get("/products")  # catalog hit
    cart = api.client.post("/cart", json={"items": [{"product_id": 1, "quantity": 1}]})
    api.client.post("/checkout", json={"cart_id": cart.json()["cart_id"]})
    api.client.get("/orders")
    api.client.get("/nowhere")
    api.cache.down = True
    api.client.get("/products")  # catalog error


def test_api_metrics_follow_the_contract(api: ApiHarness) -> None:
    drive_api_traffic(api)
    text = api.client.get("/metrics").text
    assert metric_problems(text, API_METRICS, API_BASE, require_process=ON_LINUX) == []


def test_api_app_info_carries_the_release_commit(api: ApiHarness) -> None:
    text = api.client.get("/metrics").text
    assert 'app_info{commit="606c2674b8e3"' in text


def test_worker_metrics_follow_the_contract() -> None:
    from uuid import uuid4

    metrics = WorkerMetrics(WORKER_IDENTITY)
    source, store = FakeJobSource(), FakeWorkerStore()
    order = uuid4()
    store.orders[order] = "paid"
    store.totals[order] = 1200
    source.queue = [
        {"type": "fulfil_order", "order_id": str(order)},
        {"type": "send_receipt", "order_id": str(uuid4())},  # unknown order: an error
    ]
    runner = JobRunner(source, store, metrics, batch_size=10, receipt_seconds=0)
    app = create_worker_app(WORKER_IDENTITY, runner, metrics, source, store)
    import asyncio

    asyncio.run(runner.run_once())
    runner.stop()
    with TestClient(app) as client:
        text = client.get("/metrics").text
    assert metric_problems(text, WORKER_METRICS, WORKER_BASE, require_process=ON_LINUX) == []


class _FakeProcess(Collector):
    def collect(self) -> Iterable[Metric]:
        family = GaugeMetricFamily("process_resident_memory_bytes", "Resident memory")
        family.add_metric([], 1024.0)
        yield family


def test_base_labels_reach_collector_metrics_such_as_process() -> None:
    telemetry = Telemetry(Identity("api", "1.4.0", "cs-api-140-2"), with_process=False)
    telemetry.registry.register(_FakeProcess())
    families = list(text_string_to_metric_families(telemetry.render().decode()))
    (sample,) = [s for f in families for s in f.samples]
    assert sample.labels == {"service": "api", "release": "1.4.0", "instance": "cs-api-140-2"}


def test_no_created_series() -> None:
    telemetry = Telemetry(Identity("api", "1.4.0", "cs-api-140-1", "606c2674b8e3"))
    assert isinstance(telemetry.registry, CollectorRegistry)
    assert "_created" not in telemetry.render().decode()
