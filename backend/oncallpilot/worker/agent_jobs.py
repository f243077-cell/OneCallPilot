"""The agent-job consumer of backend-worker (architecture §5.2; AI-025).

Reads ``ocp:agent:jobs`` in consumer group ``agents`` (consumer name = the
container hostname, which a restart keeps), at most ``AGENT_CONCURRENCY``
investigations at a time. Each incident is investigated at most once:

- at start-up the worker re-reads its own pending entries (``XREADGROUP … 0``);
- every cycle it ``XAUTOCLAIM``s entries idle for more than 300 s, about twice
  the slowest live run, so a run that is still alive is never taken over;
- a redelivered job whose run had started and whose incident is still
  ``investigating`` is not restarted: it escalates ``agent_interrupted``,
  keeps the evidence, and writes ``audit(agent.interrupted)``; for any other
  status the job is only acknowledged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from oncallpilot.db.agent import claim_for_investigation
from oncallpilot.escalation import escalate_incident
from oncallpilot.events import EventStream
from oncallpilot.queues import AGENT_JOBS_STREAM

log = logging.getLogger(__name__)

GROUP = "agents"
CLAIM_IDLE_MS = 300_000
AGENT_CONCURRENCY = 2

Investigate = Callable[[UUID], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class AgentJobConsumer:
    redis: Redis
    pool: asyncpg.Pool
    events: EventStream
    investigate: Investigate
    consumer: str
    run_meta: dict[str, Any]  # prompt_version, provider, model, catalogue_version, …
    concurrency: int = AGENT_CONCURRENCY
    claim_idle_ms: int = CLAIM_IDLE_MS
    clock: Callable[[], datetime] = _utcnow
    _running: set[asyncio.Task[None]] = field(default_factory=set)

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(AGENT_JOBS_STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def recover_own_pending(self) -> int:
        """Start-up: handle this consumer's entries that were never acknowledged."""
        reply: Any = await self.redis.xreadgroup(
            GROUP, self.consumer, {AGENT_JOBS_STREAM: "0"}, count=100
        )
        entries = reply[0][1] if reply else []
        for entry_id, fields in entries:
            await self.handle(str(entry_id), fields)
        return len(entries)

    async def autoclaim(self) -> int:
        reply: Any = await self.redis.xautoclaim(
            AGENT_JOBS_STREAM, GROUP, self.consumer, min_idle_time=self.claim_idle_ms, count=10
        )
        entries = reply[1] if reply else []
        for entry_id, fields in entries:
            if fields:  # entries deleted from the stream come back as None
                await self.handle(str(entry_id), fields)
        return len(entries)

    async def poll(self, block_ms: int) -> int:
        """Read new jobs while there is capacity; each runs in its own task."""
        self._running = {t for t in self._running if not t.done()}
        free = self.concurrency - len(self._running)
        if free <= 0:
            await asyncio.wait(
                self._running, timeout=block_ms / 1000, return_when=asyncio.FIRST_COMPLETED
            )
            return 0
        reply: Any = await self.redis.xreadgroup(
            GROUP, self.consumer, {AGENT_JOBS_STREAM: ">"}, count=free, block=block_ms
        )
        entries = reply[0][1] if reply else []
        for entry_id, fields in entries:
            task = asyncio.create_task(self._handle_logged(str(entry_id), fields))
            self._running.add(task)
        return len(entries)

    async def _handle_logged(self, entry_id: str, fields: dict[str, str]) -> None:
        """A crash is logged and the job stays pending; recovery then escalates it as
        ``agent_interrupted`` instead of investigating twice."""
        try:
            await self.handle(entry_id, fields)
        except Exception:
            log.exception("agent job %s failed; left pending", entry_id)

    async def drain(self) -> None:
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)
        self._running.clear()

    async def handle(self, entry_id: str, fields: dict[str, str]) -> None:
        try:
            incident_id = UUID(fields["incident_id"])
        except (KeyError, ValueError):
            log.error("malformed agent job %s; acknowledged", entry_id)
            await self._ack(entry_id)
            return
        extra = {"incident_id": incident_id}
        meta = {**self.run_meta, "run_started_at": self.clock().isoformat().replace("+00:00", "Z")}
        async with self.pool.acquire() as conn:
            decision = await claim_for_investigation(conn, incident_id, meta)
        if decision == "busy":
            log.info("incident is being claimed elsewhere; job left pending", extra=extra)
            return
        if decision == "run":
            log.info("investigation starts", extra=extra)
            await self.investigate(incident_id)
        elif decision == "interrupted":
            log.warning("redelivered job of an interrupted run", extra=extra)
            await escalate_incident(
                self.pool, self.events, incident_id, "agent_interrupted", self.clock(),
                extra_audit=("agent.interrupted", {"job": entry_id, "consumer": self.consumer}),
            )  # fmt: skip
        await self._ack(entry_id)

    async def _ack(self, entry_id: str) -> None:
        await self.redis.xack(AGENT_JOBS_STREAM, GROUP, entry_id)
