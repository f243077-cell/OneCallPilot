"""cs-loadgen (TB-005): rate schedule, request mix, control API."""

import asyncio
import json
import random
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient

from common.identity import Identity
from loadgen.app import create_app
from loadgen.traffic import MIX, RateSchedule, TrafficGenerator, choose

TOKEN = "l" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ALLOWED = {
    ("GET", "/products"),
    ("POST", "/cart"),
    ("GET", "/cart"),
    ("POST", "/checkout"),
    ("GET", "/orders"),
}


def test_baseline_is_constant() -> None:
    schedule = RateSchedule(baseline_rps=5, spike_rps=40, ramp_seconds=30)
    assert schedule.rate(0) == schedule.rate(10_000) == 5


def test_spike_ramps_linearly_then_holds_and_returns_to_baseline() -> None:
    schedule = RateSchedule(baseline_rps=5, spike_rps=45, ramp_seconds=40)
    schedule.set_mode("spike", now=100)
    assert schedule.rate(100) == 5
    assert schedule.rate(120) == pytest.approx(25)
    assert schedule.rate(140) == 45
    assert schedule.rate(1_000) == 45
    schedule.set_mode("spike", now=500)  # setting spike again does not restart the ramp
    assert schedule.rate(500) == 45
    schedule.set_mode("baseline", now=600)
    assert schedule.rate(600) == 5


def test_mix_follows_the_weights() -> None:
    rng = random.Random(3)
    seen = Counter(choose(rng) for _ in range(20_000))
    total = sum(weight for _, weight in MIX)
    for name, weight in MIX:
        assert seen[name] / 20_000 == pytest.approx(weight / total, abs=0.02)


def test_generated_requests_use_only_store_routes() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/cart" and request.method == "POST":
            return httpx.Response(201, json={"cart_id": "c-1"})
        if request.url.path == "/products":
            return httpx.Response(200, json={"products": [{"id": 7}, {"id": 8}]})
        if request.url.path == "/checkout":
            assert json.loads(request.content) == {"cart_id": "c-1"}
        return httpx.Response(200, json={})

    async def scenario() -> TrafficGenerator:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://target")
        generator = TrafficGenerator(client, RateSchedule(5, 40, 30), rng=random.Random(5))
        for _ in range(300):
            generator.fire(choose(random.Random(len(requests))))
            await asyncio.sleep(0)
        while generator._in_flight:
            await asyncio.sleep(0.001)
        await client.aclose()
        return generator

    generator = asyncio.run(scenario())
    assert set(requests) <= ALLOWED
    assert generator.counters.sent == 300
    assert generator.counters.errors == 0


def test_run_loop_sends_at_the_scheduled_rate() -> None:
    now = [0.0]
    sent: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(now[0])
        return httpx.Response(200, json={})

    async def scenario() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://target")
        generator: TrafficGenerator

        async def sleep(seconds: float) -> None:
            now[0] += seconds
            await asyncio.sleep(0)
            if now[0] >= 60:
                generator.stop()

        generator = TrafficGenerator(
            client,
            RateSchedule(5, 40, 30),
            rng=random.Random(1),
            clock=lambda: now[0],
            sleep=sleep,
        )
        await generator.run()
        await asyncio.sleep(0.01)
        await client.aclose()

    asyncio.run(scenario())
    assert len(sent) == pytest.approx(300, abs=2)  # 5 rps for 60 s


def test_control_api_needs_the_chaos_token() -> None:
    client = httpx.AsyncClient(base_url="http://target")
    generator = TrafficGenerator(client, RateSchedule(5, 40, 30))
    app = create_app(
        Identity("loadgen", "1.0.0", "cs-loadgen"),
        generator,
        client,
        chaos_token=TOKEN,
        start_traffic=False,
    )
    with TestClient(app) as control:
        assert control.get("/healthz").status_code == 200
        assert control.get("/internal/status").status_code == 401
        assert control.put("/internal/mode", json={"mode": "spike"}).status_code == 401
        assert control.get("/internal/status", headers=AUTH).json()["mode"] == "baseline"
        spiked = control.put("/internal/mode", json={"mode": "spike"}, headers=AUTH).json()
        assert spiked["mode"] == "spike"
        bad = control.put("/internal/mode", json={"mode": "max"}, headers=AUTH)
        assert bad.status_code == 422
