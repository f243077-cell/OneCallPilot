"""What the api depends on: the store database, the cache and job queue, and payments.

The real implementations live in ``pg.py``, ``rediscache.py`` and ``payments_client.py``;
tests use in-memory fakes with the same shape.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

CacheName = Literal["catalog", "pricing"]


class StoreUnavailable(Exception):
    """The store database could not serve the request (pool timeout, connection error)."""


class CacheUnavailable(Exception):
    """cs-redis could not be reached or the command failed."""


class PaymentFailed(Exception):
    """The payment provider refused the charge, failed, or timed out."""


@dataclass(frozen=True)
class Product:
    id: int
    sku: str
    name: str
    price_cents: int


@dataclass(frozen=True)
class CartLine:
    product_id: int
    quantity: int
    price_cents: int


@dataclass(frozen=True)
class Order:
    id: UUID
    cart_id: UUID
    total_cents: int
    status: str
    created_at: str


class Store(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def pool_counts(self) -> tuple[int, int]:
        """(in_use, idle) connections, for `db_pool_connections`."""
        ...

    async def list_products(self) -> list[Product]: ...
    async def prices(self, product_ids: Sequence[int]) -> dict[int, int]: ...
    async def create_cart(self, lines: Sequence[CartLine]) -> UUID: ...
    async def get_cart(self, cart_id: UUID) -> list[CartLine] | None: ...
    async def create_order(self, cart_id: UUID, total_cents: int) -> Order: ...
    async def set_order_status(self, order_id: UUID, status: str) -> None: ...
    async def list_orders(self, limit: int) -> list[Order]: ...


class Cache(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def get_catalog(self) -> list[dict[str, Any]] | None: ...
    async def set_catalog(self, products: Sequence[Mapping[str, Any]]) -> None: ...
    async def get_prices(self, product_ids: Sequence[int]) -> dict[int, int] | None:
        """All requested prices, or None when any is missing (a miss)."""
        ...

    async def set_prices(self, prices: Mapping[int, int]) -> None: ...
    async def clear(self, cache: CacheName) -> bool: ...
    async def stats(self) -> dict[str, Any]: ...
    async def enqueue(self, job: Mapping[str, Any]) -> None: ...


class Payments(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def charge(self, order_id: UUID, amount_cents: int, request_id: str) -> str: ...


@dataclass
class Deps:
    store: Store
    cache: Cache
    payments: Payments
