"""TB-004: a sample of at least 1,000 api log lines all match the C7 log contract."""

import json
import logging
import random

from common.identity import Identity
from common.jsonlog import MAX_LINE_BYTES, TRUNCATED, JsonFormatter
from tests.conftest import ApiHarness
from tests.contract import log_problems
from tests.fakes import store_unavailable

API_BASE = {"service": "api", "release": "1.4.0", "instance": "cs-api-140-1"}


def test_thousand_api_lines_match_the_contract(api: ApiHarness) -> None:
    rng = random.Random(7)
    carts: list[str] = []
    while len(api.logs.lines) < 1000:
        api.cache.down = rng.random() < 0.1
        api.payments.fail = rng.random() < 0.05
        api.store.fail = store_unavailable() if rng.random() < 0.05 else None
        if rng.random() < 0.02:
            api.store.fail = RuntimeError("unexpected state")
        action = rng.choice(["products", "cart", "checkout", "orders", "healthz", "other"])
        if action == "products":
            api.client.get("/products")
        elif action == "cart":
            response = api.client.post("/cart", json={"items": [{"product_id": 3, "quantity": 1}]})
            if response.status_code == 201:
                carts.append(response.json()["cart_id"])
        elif action == "checkout" and carts:
            api.client.post("/checkout", json={"cart_id": carts.pop()})
        elif action == "orders":
            api.client.get("/orders")
        elif action == "healthz":
            api.client.get("/healthz")
        else:
            api.client.get("/does-not-exist")

    lines = api.logs.lines
    assert len(lines) >= 1000
    problems = {i: p for i, line in enumerate(lines) if (p := log_problems(line, API_BASE))}
    assert problems == {}
    records = [json.loads(line) for line in lines]
    assert any("stacktrace" in r for r in records), "the sample has no exception lines"
    assert any(r["logger"] == "chaosshop.access" and r["level"] == "ERROR" for r in records)
    assert any(r["logger"] == "chaosshop.access" and r["level"] == "INFO" for r in records)


def make_record(msg: str, exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    return logging.LogRecord("chaosshop.test", logging.ERROR, __file__, 1, msg, None, exc_info)


def test_long_lines_are_truncated_to_16_kib() -> None:
    formatter = JsonFormatter(Identity("api", "1.4.0", "cs-api-140-1"))
    line = formatter.format(make_record("x" * 40_000))
    assert len(line.encode("utf-8")) <= MAX_LINE_BYTES
    assert json.loads(line)["msg"].endswith(TRUNCATED)
    assert log_problems(line) == []


def test_long_stacktraces_are_truncated_first() -> None:
    formatter = JsonFormatter(Identity("worker", "2.1.0", "cs-worker-210"))
    try:
        raise ValueError("é" * 30_000)
    except ValueError as exc:
        line = formatter.format(make_record("job failed", exc))
    record = json.loads(line)
    assert len(line.encode("utf-8")) <= MAX_LINE_BYTES
    assert record["msg"] == "job failed"
    assert record["stacktrace"].startswith("Traceback (most recent call last):")
    assert log_problems(line) == []


def test_timestamp_and_omitted_keys() -> None:
    formatter = JsonFormatter(Identity("payments", "1.0.0", "cs-payments"))
    record = make_record("hello")
    record.created = 1_791_000_000.123456
    data = json.loads(formatter.format(record))
    assert data["ts"].endswith(".123Z")
    assert set(data) == {"ts", "level", "service", "release", "instance", "logger", "msg"}
