"""Rate limit and cooldown, the backend's gate (architecture §5.9 checks 6–7, §5.12).

- ``ocp:ratelimit:{service}``: sorted set of execution timestamps; at most 3
  executed actions per service per 1800 s.
- ``ocp:cooldown:{service}``: set for 120 s by backend-worker when an
  execution on the service finishes; rollback proposals are exempt.

The runner keeps its own counters as a second gate.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from typing import Any

from redis.asyncio import Redis

RATE_LIMIT = 3
RATE_WINDOW_SECONDS = 1800


def ratelimit_key(service: str) -> str:
    return f"ocp:ratelimit:{service}"


def cooldown_key(service: str) -> str:
    return f"ocp:cooldown:{service}"


async def rate_limit_retry_after(redis: Redis, service: str, now: datetime) -> int | None:
    """Seconds until another execution is allowed, or None when one is allowed now."""
    key = ratelimit_key(service)
    start = now.timestamp() - RATE_WINDOW_SECONDS
    await redis.zremrangebyscore(key, "-inf", f"({start}")
    recent: Any = await redis.zrange(key, 0, -1, withscores=True)
    if len(recent) < RATE_LIMIT:
        return None
    oldest = min(float(score) for _, score in recent)
    return max(1, math.ceil(oldest + RATE_WINDOW_SECONDS - now.timestamp()))


async def cooldown_retry_after(redis: Redis, service: str) -> int | None:
    ttl: int = await redis.ttl(cooldown_key(service))
    if ttl == -2:  # no key
        return None
    return max(1, ttl)  # -1 (no expiry) also counts as active: fail closed


async def record_execution(
    redis: Redis, service: str, execution_id: uuid.UUID, now: datetime
) -> None:
    key = ratelimit_key(service)
    await redis.zadd(key, {str(execution_id): now.timestamp()})
    await redis.expire(key, RATE_WINDOW_SECONDS)
