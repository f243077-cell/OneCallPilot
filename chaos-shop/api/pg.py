"""Store database access for cs-api (cs-postgres through an asyncpg pool).

The pool feeds the C7 pool metrics: `db_pool_wait_seconds` for every acquisition,
`db_pool_timeouts_total` when one times out, and `db_pool_connections` via
``pool_counts()``.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from api.deps import CartLine, Order, Product, StoreUnavailable
from api.faults import SLOW_QUERY_SECONDS, Faults
from api.metrics import ApiMetrics

DB_ERRORS = (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError)


class DatabasePoolTimeout(StoreUnavailable):
    """No pool connection became free within the acquire timeout."""


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _order(row: Any) -> Order:
    return Order(
        id=row["id"],
        cart_id=row["cart_id"],
        total_cents=row["total_cents"],
        status=row["status"],
        created_at=iso(row["created_at"]),
    )


class PgStore:
    def __init__(
        self,
        dsn: str,
        metrics: ApiMetrics,
        faults: Faults,
        *,
        max_size: int = 5,
        acquire_timeout: float = 5.0,
        command_timeout: float = 30.0,
        connect_timeout: float = 5.0,
    ) -> None:
        self._dsn = dsn
        self._metrics = metrics
        self._faults = faults
        self._max_size = max_size
        self._acquire_timeout = acquire_timeout
        self._command_timeout = command_timeout
        self._connect_timeout = connect_timeout
        self._pool: Any = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        # The pool is created on first use, so the api starts (and reports
        # healthy) even while cs-postgres is still starting.
        return None

    async def close(self) -> None:
        if self._pool is not None:
            await asyncio.wait_for(self._pool.close(), timeout=5)

    def pool_counts(self) -> tuple[int, int]:
        if self._pool is None:
            return (0, 0)
        size = int(self._pool.get_size())
        idle = int(self._pool.get_idle_size())
        return (size - idle, idle)

    async def _get_pool(self) -> Any:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    try:
                        self._pool = await asyncpg.create_pool(
                            self._dsn,
                            min_size=1,
                            max_size=self._max_size,
                            command_timeout=self._command_timeout,
                            timeout=self._connect_timeout,
                        )
                    except DB_ERRORS as exc:
                        raise StoreUnavailable("could not connect to the store database") from exc
        return self._pool

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[Any]:
        pool = await self._get_pool()
        started = time.perf_counter()
        try:
            conn = await pool.acquire(timeout=self._acquire_timeout)
        except TimeoutError as exc:
            self._metrics.db_pool_wait.observe(time.perf_counter() - started)
            self._metrics.db_pool_timeouts.inc()
            raise DatabasePoolTimeout(
                f"timed out after {self._acquire_timeout:g}s waiting for a database connection"
            ) from exc
        except DB_ERRORS as exc:
            self._metrics.db_pool_wait.observe(time.perf_counter() - started)
            raise StoreUnavailable("could not get a database connection") from exc
        self._metrics.db_pool_wait.observe(time.perf_counter() - started)
        try:
            yield conn
        except DB_ERRORS as exc:
            raise StoreUnavailable("store database query failed") from exc
        finally:
            await pool.release(conn)

    async def list_products(self) -> list[Product]:
        async with self._connection() as conn:
            rows = await conn.fetch("SELECT id, sku, name, price_cents FROM products ORDER BY id")
        return [Product(r["id"], r["sku"], r["name"], r["price_cents"]) for r in rows]

    async def prices(self, product_ids: Sequence[int]) -> dict[int, int]:
        async with self._connection() as conn:
            rows = await conn.fetch(
                "SELECT id, price_cents FROM products WHERE id = ANY($1::int[])",
                list(product_ids),
            )
        return {r["id"]: r["price_cents"] for r in rows}

    async def create_cart(self, lines: Sequence[CartLine]) -> UUID:
        cart_id = uuid4()
        async with self._connection() as conn, conn.transaction():
            await conn.execute("INSERT INTO carts (id) VALUES ($1)", cart_id)
            await conn.executemany(
                "INSERT INTO cart_items (cart_id, product_id, quantity) VALUES ($1, $2, $3)",
                [(cart_id, line.product_id, line.quantity) for line in lines],
            )
        return cart_id

    async def get_cart(self, cart_id: UUID) -> list[CartLine] | None:
        async with self._connection() as conn:
            exists = await conn.fetchval("SELECT 1 FROM carts WHERE id = $1", cart_id)
            if exists is None:
                return None
            rows = await conn.fetch(
                "SELECT ci.product_id, ci.quantity, p.price_cents"
                " FROM cart_items ci JOIN products p ON p.id = ci.product_id"
                " WHERE ci.cart_id = $1 ORDER BY ci.product_id",
                cart_id,
            )
        return [CartLine(r["product_id"], r["quantity"], r["price_cents"]) for r in rows]

    async def create_order(self, cart_id: UUID, total_cents: int) -> Order:
        async with self._connection() as conn, conn.transaction():
            if self._faults.slow_checkout_queries:
                await conn.execute("SELECT pg_sleep($1)", float(SLOW_QUERY_SECONDS))
            row = await conn.fetchrow(
                "INSERT INTO orders (id, cart_id, total_cents, status)"
                " VALUES ($1, $2, $3, 'pending')"
                " RETURNING id, cart_id, total_cents, status, created_at",
                uuid4(),
                cart_id,
                total_cents,
            )
        return _order(row)

    async def set_order_status(self, order_id: UUID, status: str) -> None:
        async with self._connection() as conn:
            await conn.execute("UPDATE orders SET status = $2 WHERE id = $1", order_id, status)

    async def list_orders(self, limit: int) -> list[Order]:
        async with self._connection() as conn:
            rows = await conn.fetch(
                "SELECT id, cart_id, total_cents, status, created_at"
                " FROM orders ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        return [_order(r) for r in rows]
