"""Real worker backends: the cs-redis job queue and the cs-postgres store."""

import asyncio
import json
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any, TypeVar
from uuid import UUID

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import RedisError

from common.keys import CATALOG_KEY, CATALOG_TTL_SECONDS, JOB_QUEUE_KEY
from worker.jobs import DependencyUnavailable

T = TypeVar("T")
DB_ERRORS = (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError)


class RedisJobSource:
    def __init__(self, url: str) -> None:
        # The socket timeout must exceed the 1 s blocking pop.
        self._redis: Redis = Redis.from_url(
            url, socket_timeout=3.0, socket_connect_timeout=1.0, decode_responses=True
        )

    async def _call(self, pending: Awaitable[T]) -> T:
        try:
            return await pending
        except (RedisError, OSError) as exc:
            raise DependencyUnavailable("cache server unavailable") from exc

    async def pop_batch(self, max_jobs: int) -> list[Mapping[str, Any]]:
        first = await self._call(self._redis.brpop([JOB_QUEUE_KEY], timeout=1))
        if first is None:
            return []
        raw: list[str] = [str(first[1])]
        if max_jobs > 1:
            more = await self._call(self._redis.rpop(JOB_QUEUE_KEY, max_jobs - 1))
            if isinstance(more, list):
                raw.extend(str(item) for item in more)
        jobs: list[Mapping[str, Any]] = []
        for item in raw:
            try:
                job = json.loads(item)
            except ValueError:
                continue
            if isinstance(job, dict):
                jobs.append(job)
        return jobs

    async def set_catalog(self, products: Sequence[Mapping[str, Any]]) -> None:
        payload = json.dumps(list(products), separators=(",", ":"))
        await self._call(self._redis.set(CATALOG_KEY, payload, ex=CATALOG_TTL_SECONDS))

    async def close(self) -> None:
        await self._redis.aclose()


class PgWorkerStore:
    def __init__(self, dsn: str, *, timeout: float = 5.0) -> None:
        self._dsn = dsn
        self._timeout = timeout
        self._pool: Any = None
        self._lock = asyncio.Lock()

    async def _get_pool(self) -> Any:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    try:
                        self._pool = await asyncpg.create_pool(
                            self._dsn,
                            min_size=1,
                            max_size=2,
                            command_timeout=30,
                            timeout=self._timeout,
                        )
                    except DB_ERRORS as exc:
                        raise DependencyUnavailable("could not connect to the database") from exc
        return self._pool

    async def _fetch(self, query: str, *args: Any) -> list[Any]:
        pool = await self._get_pool()
        try:
            async with pool.acquire(timeout=self._timeout) as conn:
                return list(await conn.fetch(query, *args))
        except DB_ERRORS as exc:
            raise DependencyUnavailable("database query failed") from exc

    async def mark_fulfilled(self, order_id: UUID) -> bool:
        rows = await self._fetch(
            "UPDATE orders SET status = 'fulfilled', fulfilled_at = now()"
            " WHERE id = $1 RETURNING id",
            order_id,
        )
        return bool(rows)

    async def order_total(self, order_id: UUID) -> int | None:
        rows = await self._fetch("SELECT total_cents FROM orders WHERE id = $1", order_id)
        return int(rows[0]["total_cents"]) if rows else None

    async def products(self) -> list[dict[str, Any]]:
        rows = await self._fetch("SELECT id, sku, name, price_cents FROM products ORDER BY id")
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._pool is not None:
            await asyncio.wait_for(self._pool.close(), timeout=5)
