import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.deps import Deps
from api.faults import Faults
from api.metrics import ApiMetrics
from common.identity import Identity
from common.jsonlog import JsonFormatter
from tests.fakes import FakeCache, FakePayments, FakeStore

API_IDENTITY = Identity("api", "1.4.0", "cs-api-140-1", "606c2674b8e3")
ADMIN_TOKEN = "a" * 32
CHAOS_TOKEN = "c" * 32


class ListHandler(logging.Handler):
    def __init__(self, formatter: logging.Formatter) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(formatter)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def capture(identity: Identity) -> Iterator[ListHandler]:
    handler = ListHandler(JsonFormatter(identity))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


@pytest.fixture
def api_logs() -> Iterator[ListHandler]:
    yield from capture(API_IDENTITY)


@dataclass
class ApiHarness:
    client: TestClient
    store: FakeStore
    cache: FakeCache
    payments: FakePayments
    metrics: ApiMetrics
    logs: ListHandler
    faults: Faults
    lines: list[str] = field(default_factory=list)


def make_api(identity: Identity, logs: ListHandler) -> Iterator[ApiHarness]:
    metrics = ApiMetrics(identity)
    store = FakeStore(metrics)
    cache = FakeCache()
    payments = FakePayments()
    faults = Faults()
    app = create_app(
        identity,
        Deps(store=store, cache=cache, payments=payments),
        metrics,
        runner_admin_token=ADMIN_TOKEN,
        chaos_token=CHAOS_TOKEN,
        faults=faults,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield ApiHarness(client, store, cache, payments, metrics, logs, faults)


@pytest.fixture
def api(api_logs: ListHandler) -> Iterator[ApiHarness]:
    yield from make_api(API_IDENTITY, api_logs)
