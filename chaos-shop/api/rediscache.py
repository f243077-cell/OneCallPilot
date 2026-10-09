"""cs-redis access for cs-api: the `catalog` and `pricing` caches and the job queue."""

import json
from collections.abc import Awaitable, Mapping, Sequence
from typing import Any, TypeVar

from redis.asyncio import Redis
from redis.exceptions import RedisError

from api.deps import CacheName, CacheUnavailable
from common.keys import (
    CATALOG_KEY,
    CATALOG_TTL_SECONDS,
    JOB_QUEUE_KEY,
    PRICING_KEY,
    PRICING_TTL_SECONDS,
)

T = TypeVar("T")

KEYS: dict[str, str] = {"catalog": CATALOG_KEY, "pricing": PRICING_KEY}


class RedisCache:
    def __init__(self, url: str, *, timeout: float = 1.0) -> None:
        self._redis: Redis = Redis.from_url(
            url,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            decode_responses=True,
        )

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        await self._redis.aclose()

    async def _call(self, pending: Awaitable[T]) -> T:
        try:
            return await pending
        except (RedisError, OSError) as exc:
            raise CacheUnavailable("cache server unavailable") from exc

    async def get_catalog(self) -> list[dict[str, Any]] | None:
        raw = await self._call(self._redis.get(CATALOG_KEY))
        if raw is None:
            return None
        products: list[dict[str, Any]] = json.loads(raw)
        return products

    async def set_catalog(self, products: Sequence[Mapping[str, Any]]) -> None:
        payload = json.dumps(list(products), separators=(",", ":"))
        await self._call(self._redis.set(CATALOG_KEY, payload, ex=CATALOG_TTL_SECONDS))

    async def get_prices(self, product_ids: Sequence[int]) -> dict[int, int] | None:
        fields = [str(i) for i in product_ids]
        values: list[str | None] = await self._call(
            self._redis.hmget(PRICING_KEY, fields)  # type: ignore[arg-type]
        )
        if any(v is None for v in values):
            return None
        return {pid: int(v) for pid, v in zip(product_ids, values, strict=True) if v is not None}

    async def set_prices(self, prices: Mapping[int, int]) -> None:
        if not prices:
            return
        pipe = self._redis.pipeline(transaction=True)
        pipe.hset(PRICING_KEY, mapping={str(k): v for k, v in prices.items()})
        pipe.expire(PRICING_KEY, PRICING_TTL_SECONDS)
        await self._call(pipe.execute())

    async def clear(self, cache: CacheName) -> bool:
        deleted: int = await self._call(self._redis.delete(KEYS[cache]))
        return deleted > 0

    async def stats(self) -> dict[str, Any]:
        catalog_present: int = await self._call(self._redis.exists(CATALOG_KEY))
        catalog_ttl: int = await self._call(self._redis.ttl(CATALOG_KEY))
        pricing_entries: int = await self._call(self._redis.hlen(PRICING_KEY))
        pricing_ttl: int = await self._call(self._redis.ttl(PRICING_KEY))
        return {
            "catalog": {"present": catalog_present > 0, "ttl_seconds": max(catalog_ttl, 0)},
            "pricing": {"entries": pricing_entries, "ttl_seconds": max(pricing_ttl, 0)},
        }

    async def enqueue(self, job: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(job), separators=(",", ":"))
        await self._call(self._redis.lpush(JOB_QUEUE_KEY, payload))
