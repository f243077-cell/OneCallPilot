"""In-memory stand-ins for cs-postgres, cs-redis and cs-payments."""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from api.deps import (
    CacheName,
    CacheUnavailable,
    CartLine,
    Order,
    PaymentFailed,
    Product,
    StoreUnavailable,
)
from api.metrics import ApiMetrics
from worker.jobs import DependencyUnavailable


class FakeStore:
    def __init__(self, metrics: ApiMetrics | None = None) -> None:
        self.metrics = metrics
        self.products = [Product(i, f"SKU-{i:03d}", f"Product {i}", 1000 + i) for i in range(1, 6)]
        self.carts: dict[UUID, list[CartLine]] = {}
        self.orders: dict[UUID, Order] = {}
        self.fail: Exception | None = None
        self.price_reads = 0

    def _op(self) -> None:
        if self.metrics is not None:
            self.metrics.db_pool_wait.observe(0.001)
        if self.fail is not None:
            raise self.fail

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def pool_counts(self) -> tuple[int, int]:
        return (1, 4)

    async def list_products(self) -> list[Product]:
        self._op()
        return list(self.products)

    async def prices(self, product_ids: Sequence[int]) -> dict[int, int]:
        self._op()
        self.price_reads += 1
        return {p.id: p.price_cents for p in self.products if p.id in product_ids}

    async def create_cart(self, lines: Sequence[CartLine]) -> UUID:
        self._op()
        cart_id = uuid4()
        self.carts[cart_id] = list(lines)
        return cart_id

    async def get_cart(self, cart_id: UUID) -> list[CartLine] | None:
        self._op()
        return self.carts.get(cart_id)

    async def create_order(self, cart_id: UUID) -> Order | None:
        self._op()
        lines = self.carts.get(cart_id)
        if not lines:
            return None
        total = sum(line.quantity * line.price_cents for line in lines)
        order = Order(uuid4(), cart_id, total, "pending", "2026-10-09T10:00:00.000Z")
        self.orders[order.id] = order
        return order

    async def set_order_status(self, order_id: UUID, status: str) -> None:
        self._op()
        old = self.orders[order_id]
        self.orders[order_id] = Order(old.id, old.cart_id, old.total_cents, status, old.created_at)

    async def list_orders(self, limit: int) -> list[Order]:
        self._op()
        return list(self.orders.values())[-limit:]


class FakeCache:
    def __init__(self) -> None:
        self.down = False
        self.catalog: list[dict[str, Any]] | None = None
        self.prices: dict[int, int] = {}
        self.jobs: list[dict[str, Any]] = []

    def _check(self) -> None:
        if self.down:
            raise CacheUnavailable("cache server unavailable") from ConnectionError(
                "Error connecting to cache"
            )

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def get_catalog(self) -> list[dict[str, Any]] | None:
        self._check()
        return self.catalog

    async def set_catalog(self, products: Sequence[Mapping[str, Any]]) -> None:
        self._check()
        self.catalog = [dict(p) for p in products]

    async def get_prices(self, product_ids: Sequence[int]) -> dict[int, int] | None:
        self._check()
        if all(pid in self.prices for pid in product_ids):
            return {pid: self.prices[pid] for pid in product_ids}
        return None

    async def set_prices(self, prices: Mapping[int, int]) -> None:
        self._check()
        self.prices.update(prices)

    async def clear(self, cache: CacheName) -> bool:
        self._check()
        if cache == "catalog":
            had = self.catalog is not None
            self.catalog = None
            return had
        had = bool(self.prices)
        self.prices.clear()
        return had

    async def stats(self) -> dict[str, Any]:
        self._check()
        return {
            "catalog": {"present": self.catalog is not None, "ttl_seconds": 0},
            "pricing": {"entries": len(self.prices), "ttl_seconds": 0},
        }

    async def enqueue(self, job: Mapping[str, Any]) -> None:
        self._check()
        self.jobs.append(dict(job))


class FakePayments:
    def __init__(self) -> None:
        self.fail = False
        self.calls: list[tuple[UUID, int, str]] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def charge(self, order_id: UUID, amount_cents: int, request_id: str) -> str:
        self.calls.append((order_id, amount_cents, request_id))
        if self.fail:
            raise PaymentFailed("payment provider returned HTTP 503")
        return uuid4().hex


class FakeJobSource:
    def __init__(self) -> None:
        self.queue: list[Mapping[str, Any]] = []
        self.catalog: list[Mapping[str, Any]] | None = None
        self.down = False
        self.closed = False

    async def pop_batch(self, max_jobs: int) -> list[Mapping[str, Any]]:
        if self.down:
            raise DependencyUnavailable("cache server unavailable")
        batch, self.queue = self.queue[:max_jobs], self.queue[max_jobs:]
        return batch

    async def set_catalog(self, products: Sequence[Mapping[str, Any]]) -> None:
        if self.down:
            raise DependencyUnavailable("cache server unavailable")
        self.catalog = list(products)

    async def close(self) -> None:
        self.closed = True


class FakeWorkerStore:
    def __init__(self) -> None:
        self.orders: dict[UUID, str] = {}
        self.totals: dict[UUID, int] = {}
        self.down = False

    def _check(self) -> None:
        if self.down:
            raise DependencyUnavailable("database query failed")

    async def mark_fulfilled(self, order_id: UUID) -> bool:
        self._check()
        if order_id not in self.orders:
            return False
        self.orders[order_id] = "fulfilled"
        return True

    async def order_total(self, order_id: UUID) -> int | None:
        self._check()
        return self.totals.get(order_id)

    async def products(self) -> list[dict[str, Any]]:
        self._check()
        return [{"id": 1, "sku": "SKU-001", "name": "Product 1", "price_cents": 1001}]

    async def close(self) -> None:
        return None


def store_unavailable() -> StoreUnavailable:
    try:
        raise TimeoutError("pool exhausted")
    except TimeoutError as cause:
        error = StoreUnavailable("timed out waiting for a database connection")
        error.__cause__ = cause
        return error
