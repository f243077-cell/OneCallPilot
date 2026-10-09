"""Open-loop traffic: requests start on a fixed schedule whether or not earlier ones finished.

Open loop matters for the spike scenario: a saturated api slows down, but the
offered load does not drop, just as real customers keep arriving.
"""

import asyncio
import logging
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx

log = logging.getLogger("chaosshop.loadgen")

Mode = Literal["baseline", "spike"]

# Request mix: one tick sends one request, so the request rate equals the tick rate.
MIX: tuple[tuple[str, int], ...] = (
    ("products", 40),
    ("cart_create", 25),
    ("cart_get", 10),
    ("checkout", 15),
    ("orders", 10),
)
DEFAULT_PRODUCT_IDS = tuple(range(1, 21))
MAX_IN_FLIGHT = 256
MAX_LAG_SECONDS = 1.0
SUMMARY_SECONDS = 60.0


@dataclass
class RateSchedule:
    """Requests per second over time: baseline, or a linear ramp up to the spike rate."""

    baseline_rps: float
    spike_rps: float
    ramp_seconds: float
    mode: Mode = "baseline"
    spike_started: float = 0.0

    def set_mode(self, mode: Mode, now: float) -> None:
        if mode == "spike" and self.mode != "spike":
            self.spike_started = now
        self.mode = mode

    def rate(self, now: float) -> float:
        if self.mode == "baseline":
            return self.baseline_rps
        if self.ramp_seconds <= 0:
            return self.spike_rps
        progress = min(1.0, max(0.0, (now - self.spike_started) / self.ramp_seconds))
        return self.baseline_rps + (self.spike_rps - self.baseline_rps) * progress


def choose(rng: random.Random) -> str:
    names = [name for name, _ in MIX]
    weights = [weight for _, weight in MIX]
    return rng.choices(names, weights=weights, k=1)[0]


@dataclass
class Counters:
    sent: int = 0
    ok: int = 0
    errors: int = 0
    dropped: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


class TrafficGenerator:
    def __init__(
        self,
        client: httpx.AsyncClient,
        schedule: RateSchedule,
        *,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._client = client
        self.schedule = schedule
        self._rng = rng or random.Random()
        self._clock = clock
        self._sleep = sleep
        self._carts: deque[str] = deque(maxlen=200)
        self._product_ids: tuple[int, ...] = DEFAULT_PRODUCT_IDS
        self._in_flight: set[asyncio.Task[None]] = set()
        self.counters = Counters()
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        log.info(
            "traffic started: baseline %.1f rps, spike %.1f rps, ramp %.0f s",
            self.schedule.baseline_rps,
            self.schedule.spike_rps,
            self.schedule.ramp_seconds,
        )
        next_tick = self._clock()
        next_summary = next_tick + SUMMARY_SECONDS
        while not self._stopping.is_set():
            now = self._clock()
            if now >= next_summary:
                next_summary = now + SUMMARY_SECONDS
                self._log_summary()
            if next_tick < now - MAX_LAG_SECONDS:
                next_tick = now  # never burst to catch up after a stall
            if next_tick > now:
                await self._sleep(next_tick - now)
            self.fire(choose(self._rng))
            next_tick += 1.0 / max(self.schedule.rate(self._clock()), 0.1)
        for task in list(self._in_flight):
            task.cancel()

    def fire(self, action: str) -> None:
        if len(self._in_flight) >= MAX_IN_FLIGHT:
            self.counters.dropped += 1
            return
        task = asyncio.create_task(self._request(action))
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

    async def _request(self, action: str) -> None:
        self.counters.sent += 1
        try:
            response = await self._send(action)
        except httpx.HTTPError:
            self.counters.errors += 1
            return
        status = str(response.status_code)
        self.counters.by_status[status] = self.counters.by_status.get(status, 0) + 1
        if response.status_code >= 500:
            self.counters.errors += 1
        else:
            self.counters.ok += 1

    async def _send(self, action: str) -> httpx.Response:
        if action == "products":
            response = await self._client.get("/products")
            if response.status_code == 200:
                self._remember_products(response)
            return response
        if action == "cart_create" or (action in ("cart_get", "checkout") and not self._carts):
            return await self._create_cart()
        if action == "cart_get":
            return await self._client.get("/cart", params={"cart_id": self._carts[-1]})
        if action == "checkout":
            return await self._client.post("/checkout", json={"cart_id": self._carts.popleft()})
        return await self._client.get("/orders", params={"limit": 10})

    async def _create_cart(self) -> httpx.Response:
        count = self._rng.randint(1, 3)
        product_ids = self._rng.sample(self._product_ids, k=min(count, len(self._product_ids)))
        items = [{"product_id": pid, "quantity": self._rng.randint(1, 3)} for pid in product_ids]
        response = await self._client.post("/cart", json={"items": items})
        if response.status_code == 201:
            cart_id = response.json().get("cart_id")
            if isinstance(cart_id, str):
                self._carts.append(cart_id)
        return response

    def _remember_products(self, response: httpx.Response) -> None:
        try:
            ids = tuple(int(p["id"]) for p in response.json()["products"])
        except (ValueError, KeyError, TypeError):
            return
        if ids:
            self._product_ids = ids

    def _log_summary(self) -> None:
        c = self.counters
        log.info(
            "traffic %s at %.1f rps: sent %d, ok %d, errors %d, dropped %d, in flight %d",
            self.schedule.mode,
            self.schedule.rate(self._clock()),
            c.sent,
            c.ok,
            c.errors,
            c.dropped,
            len(self._in_flight),
        )
