"""Redis keys of copilot-redis other than ``ocp:events`` (architecture §5.12)."""

from __future__ import annotations

from uuid import UUID

from redis.asyncio import Redis

AGENT_JOBS_STREAM = "ocp:agent:jobs"
BENCH_RUN_KEY = "ocp:bench:current_run"


class Queues:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def enqueue_agent_job(self, incident_id: UUID) -> str:
        """``XADD ocp:agent:jobs {incident_id}``; the worker group ``agents`` reads it."""
        return str(await self._redis.xadd(AGENT_JOBS_STREAM, {"incident_id": str(incident_id)}))

    async def current_bench_run(self) -> str | None:
        """The benchmark run to stamp on new incidents (set by the ``bench`` CLI)."""
        value = await self._redis.get(BENCH_RUN_KEY)
        return str(value) if value else None
