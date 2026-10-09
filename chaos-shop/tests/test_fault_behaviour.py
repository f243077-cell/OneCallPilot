"""Service-side fault behaviour (architecture §11.3, ADR-19, TB-008).

The chaos CLI only flips these switches; this file proves each one does what the
scenario needs, and that state meant to survive a restart does.
"""

import asyncio
import json
import random
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api.deps import CartLine
from api.pricing import apply_coupon, coupon_percent, order_total_cents
from common.identity import Identity
from loadgen.app import create_app as create_loadgen_app
from loadgen.traffic import CRAFTED_COUPONS, RateSchedule, TrafficGenerator
from tests.conftest import CHAOS_TOKEN, ApiHarness, capture, make_api
from tests.contract import log_problems
from worker.retention import ResultRetention

AUTH = {"Authorization": f"Bearer {CHAOS_TOKEN}"}
API_150 = Identity("api", "1.5.0", "cs-api-150-1", "e56a282e7523")


def lines(*totals: int) -> list[CartLine]:
    return [CartLine(product_id=i + 1, quantity=1, price_cents=t) for i, t in enumerate(totals)]


# --- Scenario 2: release 1.5.0 checkout totals ---


def test_release_140_sums_the_cart() -> None:
    assert order_total_cents(lines(1999, 7001), "1.4.0") == 9000


def test_release_150_rounds_small_orders_and_fails_from_50_00() -> None:
    assert order_total_cents(lines(1234), "1.5.0") % 10 == 0
    assert order_total_cents(lines(1234), "1.5.0") >= 1234
    with pytest.raises(IndexError):
        order_total_cents(lines(2500, 2500), "1.5.0")


@pytest.fixture
def api_150() -> Iterator[ApiHarness]:
    for logs in capture(API_150):
        yield from make_api(API_150, logs)


def test_release_150_checkout_returns_500_with_a_stacktrace(api_150: ApiHarness) -> None:
    cart = api_150.client.post("/cart", json={"items": [{"product_id": 5, "quantity": 10}]})
    response = api_150.client.post("/checkout", json={"cart_id": cart.json()["cart_id"]})
    assert response.status_code == 500
    records = [json.loads(line) for line in api_150.logs.lines]
    assert any(r.get("exc_type") == "IndexError" for r in records)
    assert all(log_problems(line) == [] for line in api_150.logs.lines)


def test_release_140_checkout_of_the_same_cart_works(api: ApiHarness) -> None:
    cart = api.client.post("/cart", json={"items": [{"product_id": 5, "quantity": 10}]})
    assert api.client.post("/checkout", json={"cart_id": cart.json()["cart_id"]}).status_code == 201


# --- Scenario 8: coupon parser, both releases ---


def test_known_coupons_give_their_discount() -> None:
    assert coupon_percent("WELCOME-10") == 10
    assert coupon_percent("spring-15") == 15
    assert coupon_percent("WELCOME-50") == 0
    assert apply_coupon(1000, 15) == 850


@pytest.mark.parametrize("code", CRAFTED_COUPONS)
def test_crafted_coupons_fail_and_their_text_reaches_the_log(api: ApiHarness, code: str) -> None:
    cart = api.client.post("/cart", json={"items": [{"product_id": 1, "quantity": 1}]})
    response = api.client.post(
        "/checkout", json={"cart_id": cart.json()["cart_id"], "coupon": code}
    )
    assert response.status_code == 500
    records = [json.loads(line) for line in api.logs.lines]
    assert any(code in r["msg"] for r in records)  # the INFO line with the code
    errors = [r for r in records if r.get("exc_type") == "ValueError"]
    assert errors and code in errors[0]["exc_message"]
    assert all(log_problems(line) == [] for line in api.logs.lines)


# --- Scenario 3: slow checkout queries switch ---


def test_slow_query_switch_needs_the_chaos_token(api: ApiHarness) -> None:
    url = "/internal/chaos/slow-queries"
    assert api.client.put(url, json={"enabled": True}).status_code == 401
    assert api.client.put(url, json={"enabled": True}, headers=AUTH).json() == {"enabled": True}
    assert api.faults.slow_checkout_queries
    assert api.client.get(url, headers=AUTH).json() == {"enabled": True}
    # Not counted, not access-logged.
    assert "slow-queries" not in api.client.get("/metrics").text
    app_lines = [line for line in api.logs.lines if '"logger":"chaosshop.' in line]
    assert not any("/internal" in line or "slow-queries" in line for line in app_lines)


# --- Scenario 1: worker result retention ---


def test_retention_grows_only_while_the_marker_exists(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    retention = ResultRetention(marker, chunk_bytes=1024)
    retention.grow_once()
    assert retention.held_bytes == 0
    retention.enable()
    for _ in range(3):
        retention.grow_once()
    assert retention.held_bytes == 3 * 1024


def test_marker_survives_a_kill_but_not_a_graceful_stop(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    ResultRetention(marker).enable()
    # A SIGKILL runs no handler: the next process finds the marker and grows again.
    after_kill = ResultRetention(marker, chunk_bytes=1024)
    after_kill.grow_once()
    assert after_kill.enabled and after_kill.held_bytes == 1024
    # SIGTERM runs the shutdown handler, which removes it.
    after_kill.on_shutdown()
    assert not marker.exists()
    assert not ResultRetention(marker).enabled


# --- Scenarios 5 and 8: loadgen modes survive a restart ---


def make_loadgen(state_file: Path) -> tuple[TestClient, TrafficGenerator]:
    client = httpx.AsyncClient(base_url="http://target")
    generator = TrafficGenerator(client, RateSchedule(5, 40, 30))
    app = create_loadgen_app(
        Identity("loadgen", "1.0.0", "cs-loadgen"),
        generator,
        client,
        chaos_token=CHAOS_TOKEN,
        start_traffic=False,
        state_file=state_file,
    )
    return TestClient(app), generator


def test_loadgen_mode_and_coupon_stream_survive_a_restart(tmp_path: Path) -> None:
    state_file = tmp_path / "loadgen.json"
    control, _ = make_loadgen(state_file)
    with control:
        control.put("/internal/mode", json={"mode": "spike"}, headers=AUTH)
        stream = control.put("/internal/coupon-stream", json={"enabled": True}, headers=AUTH)
        assert stream.json()["coupon_stream"] is True
    restarted, generator = make_loadgen(state_file)
    with restarted:
        status = restarted.get("/internal/status", headers=AUTH).json()
    assert status["mode"] == "spike" and status["coupon_stream"] is True
    assert generator.coupon_stream


def test_coupon_stream_sends_crafted_checkouts_at_two_per_second() -> None:
    now = [0.0]
    checkouts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cart" and request.method == "POST":
            return httpx.Response(201, json={"cart_id": "c-1"})
        if request.url.path == "/checkout":
            coupon = json.loads(request.content).get("coupon")
            if coupon in CRAFTED_COUPONS:
                checkouts.append(coupon)
        return httpx.Response(200, json={})

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://t")
        generator: TrafficGenerator

        async def sleep(seconds: float) -> None:
            now[0] += seconds
            await asyncio.sleep(0)
            if now[0] >= 30:
                generator.stop()

        generator = TrafficGenerator(
            client,
            RateSchedule(5, 40, 30),
            rng=random.Random(2),
            clock=lambda: now[0],
            sleep=sleep,
        )
        generator.set_coupon_stream(True)
        await generator.run()
        await asyncio.sleep(0.01)
        await client.aclose()

    asyncio.run(scenario())
    assert len(checkouts) == pytest.approx(60, abs=3)  # 2 per second for 30 s
