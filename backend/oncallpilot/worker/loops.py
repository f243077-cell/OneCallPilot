"""backend-worker loops after an approval (architecture §5.10 steps 6–8; FR-013, FR-014, FR-024).

- ``RunnerResultConsumer``: ``ocp:runner:results`` in consumer group
  ``backend``. Every entry's signature is verified with ``RUNNER_LINK_KEY``
  (RUN-015); an unsigned, forged or malformed entry is logged and dropped.
  ``dry_run_result`` entries are only acknowledged (``RunnerLink`` reads them
  with ``XREAD``).
- ``SignalVerifier``: every cycle, the incidents in ``verifying`` (database
  state, so a restart strands nothing): the triggering signals must stay
  normal for ``VERIFY_WINDOW_SECONDS`` within ``VERIFY_TIMEOUT_SECONDS`` of
  the execution's end. Progress is kept in ``executions.output.verify``.
- ``Sweeper``: every 10 s, expire pending proposals and end executions with
  no final result after ``EXECUTION_TIMEOUT_SECONDS``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from oncallpilot_contracts.runner import RESULTS_STREAM, STREAM_FIELD, RunnerResult
from oncallpilot_contracts.signing import verify
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError

from oncallpilot.db import executions as db
from oncallpilot.detector.config import DetectorConfig
from oncallpilot.detector.engine import Samples
from oncallpilot.detector.verify import signal_normal
from oncallpilot.proposal_store import iso
from oncallpilot.worker.outcomes import Outcomes

log = logging.getLogger(__name__)

RESULTS_GROUP = "backend"
VERIFY_WINDOW_SECONDS = 60
VERIFY_TIMEOUT_SECONDS = 300
EXECUTION_TIMEOUT_SECONDS = 240
SWEEP_EVERY_SECONDS = 10.0
SAMPLES_MAX_AGE_SECONDS = 30.0


@dataclass
class RunnerResultConsumer:
    redis: Redis
    outcomes: Outcomes
    link_key: bytes
    consumer: str

    async def ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(RESULTS_STREAM, RESULTS_GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def poll(self, block_ms: int, pending: bool = False) -> int:
        """Handle a batch; ``pending`` re-reads this consumer's unacknowledged entries."""
        reply: Any = await self.redis.xreadgroup(
            RESULTS_GROUP,
            self.consumer,
            {RESULTS_STREAM: "0" if pending else ">"},
            count=20,
            block=None if pending else block_ms,
        )
        entries = reply[0][1] if reply else []
        for entry_id, fields in entries:
            await self.handle(fields)
            await self.redis.xack(RESULTS_STREAM, RESULTS_GROUP, entry_id)
        return len(entries)

    async def handle(self, fields: dict[str, str] | None) -> None:
        result = self.parse(fields)
        if result is None or result.type == "dry_run_result":
            return
        if result.type == "progress":
            await self.outcomes.progress(result)
        else:
            await self.outcomes.final(result)

    def parse(self, fields: dict[str, str] | None) -> RunnerResult | None:
        try:
            raw = json.loads((fields or {})[STREAM_FIELD])
        except (KeyError, ValueError):
            log.warning("dropped an unreadable runner result")
            return None
        if not isinstance(raw, dict) or not verify(raw, self.link_key):
            log.warning("dropped a runner result with a missing or bad signature")
            return None
        try:
            return RunnerResult.model_validate(raw)
        except ValidationError:
            log.warning("dropped a malformed runner result")
            return None


@dataclass
class LatestSamples:
    """The detector's newest samples, shared with the verifier in the same process."""

    at: datetime | None = None
    samples: dict[str, Samples] = field(default_factory=dict)

    def update(self, at: datetime, samples: dict[str, Samples]) -> None:
        self.at, self.samples = at, samples

    def get(self, service: str, now: datetime) -> Samples | None:
        if self.at is None or (now - self.at).total_seconds() > SAMPLES_MAX_AGE_SECONDS:
            return None
        return self.samples.get(service)


def triggering_signals(trigger: dict[str, Any]) -> dict[str, float | None]:
    """Signal name → the baseline recorded when it first fired."""
    out: dict[str, float | None] = {}
    for alert in [trigger, *trigger.get("updates", [])]:
        for signal in alert.get("signals", []):
            out.setdefault(str(signal["name"]), signal.get("baseline"))
    return out


@dataclass
class SignalVerifier:
    pool: asyncpg.Pool
    outcomes: Outcomes
    config: DetectorConfig
    latest: LatestSamples
    clock: Callable[[], datetime]
    window_seconds: int = VERIFY_WINDOW_SECONDS
    timeout_seconds: int = VERIFY_TIMEOUT_SECONDS

    async def cycle(self) -> None:
        async with self.pool.acquire() as conn:
            ids = await db.verifying(conn)
        for execution_id in ids:
            await self.check(execution_id)

    async def check(self, execution_id: Any) -> None:
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            row = await db.lock_execution(conn, execution_id)
            if row is None or row["incident_status"] != "verifying":
                return
            signals = triggering_signals(row["trigger"])
            sample = self.latest.get(row["service"], now)
            normal = all(
                signal_normal(self.config, name, sample, baseline)
                for name, baseline in signals.items()
            )
            output = dict(row["output"] or {})
            state = dict(output.get("verify") or {})
            since = state.get("normal_since") if normal else None
            if normal and since is None:
                since = iso(now)
            state.update(normal_since=since, checked_at=iso(now))
            output["verify"] = state
            await db.set_health(conn, execution_id, dict(row["health_after"] or {}), output)
            finished: datetime = row["finished_at"]
        passed = since is not None and now - datetime.fromisoformat(
            since.replace("Z", "+00:00")
        ) >= timedelta(seconds=self.window_seconds)
        timed_out = now >= finished + timedelta(seconds=self.timeout_seconds)
        if passed or timed_out:
            verdict = {
                "passed": passed,
                "checked_at": iso(now),
                "window_seconds": self.window_seconds,
                "signals": sorted(signals),
            }
            await self.outcomes.verified(execution_id, verdict, passed)


@dataclass
class Sweeper:
    pool: asyncpg.Pool
    outcomes: Outcomes
    clock: Callable[[], datetime]
    execution_timeout_seconds: float = EXECUTION_TIMEOUT_SECONDS

    async def cycle(self) -> None:
        now = self.clock()
        async with self.pool.acquire() as conn:
            expired = await db.expired_pending(conn, now)
            stuck = await db.timed_out(
                conn, now - timedelta(seconds=self.execution_timeout_seconds)
            )
        for proposal_id in expired:
            await self.outcomes.expire(proposal_id)
        for execution_id in stuck:
            await self.outcomes.time_out(execution_id)


async def every(seconds: float, step: Callable[[], Any], name: str) -> None:
    """Run ``step`` forever, logging (not raising) database and Redis errors."""
    while True:
        try:
            await step()
        except (asyncpg.PostgresError, OSError, RedisError) as exc:
            log.error("%s cycle failed: %s", name, type(exc).__name__)
        await asyncio.sleep(seconds)
