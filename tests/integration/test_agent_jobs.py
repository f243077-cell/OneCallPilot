"""The agent-job consumer against Postgres and Redis (AI-025; architecture §5.2)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from oncallpilot_contracts.api import AlertIn
from redis.asyncio import Redis

from oncallpilot.db.ingest import ingest_alert
from oncallpilot.events import EventStream
from oncallpilot.queues import AGENT_JOBS_STREAM, Queues
from oncallpilot.worker.agent_jobs import GROUP, AgentJobConsumer

NOW = datetime(2026, 10, 21, 10, 5, tzinfo=UTC)
META = {"prompt_version": "v1", "provider": "fake", "model": "fake-1"}


async def incident(pool: asyncpg.Pool, service: str = "api") -> uuid.UUID:
    alert = AlertIn.model_validate(
        {
            "source": "detector",
            "service": service,
            "signals": [
                {
                    "name": "error_rate",
                    "value": 0.3,
                    "baseline": 0.004,
                    "zscore": 9.0,
                    "first_anomalous_at": "2026-10-21T10:00:00Z",
                }
            ],
            "observed_at": "2026-10-21T10:00:15Z",
        }
    )
    async with pool.acquire() as conn:
        outcome = await ingest_alert(
            conn, alert, now=NOW, detector_config_version="t", benchmark_run_id=None
        )
    return outcome.incident_id


class Recorder:
    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.runs: list[uuid.UUID] = []
        self.delay = delay
        self.fail = fail

    async def __call__(self, incident_id: uuid.UUID) -> None:
        self.runs.append(incident_id)
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("worker died")


def consumer(
    redis: Redis, pool: asyncpg.Pool, run: Recorder, name: str = "w1", idle_ms: int = 300_000
) -> AgentJobConsumer:
    return AgentJobConsumer(
        redis=redis,
        pool=pool,
        events=EventStream(redis),
        investigate=run,
        consumer=name,
        run_meta=META,
        claim_idle_ms=idle_ms,
        clock=lambda: NOW,
    )


async def pending(redis: Redis) -> int:
    info: Any = await redis.xpending(AGENT_JOBS_STREAM, GROUP)
    return int(info["pending"])


async def test_a_job_runs_once_and_is_acknowledged(pool: asyncpg.Pool, redis_client: Redis) -> None:
    incident_id = await incident(pool)
    await Queues(redis_client).enqueue_agent_job(incident_id)
    run = Recorder()
    c = consumer(redis_client, pool, run)
    await c.ensure_group()
    await c.ensure_group()  # idempotent
    assert await c.poll(block_ms=100) == 1
    await c.drain()
    assert run.runs == [incident_id]
    assert await pending(redis_client) == 0
    meta = await pool.fetchval("select agent_meta from ocp.incidents where id = $1", incident_id)
    assert meta["run_started_at"] == "2026-10-21T10:05:00Z" and meta["prompt_version"] == "v1"


async def test_two_workers_and_a_duplicate_job_make_one_investigation(
    pool: asyncpg.Pool, redis_client: Redis
) -> None:
    incident_id = await incident(pool)
    queues = Queues(redis_client)
    await queues.enqueue_agent_job(incident_id)
    await queues.enqueue_agent_job(incident_id)  # enqueued twice by mistake
    run = Recorder(delay=0.05)
    first, second = consumer(redis_client, pool, run, "w1"), consumer(redis_client, pool, run, "w2")
    await first.ensure_group()
    await asyncio.gather(first.poll(100), second.poll(100))
    await asyncio.gather(first.drain(), second.drain())
    assert run.runs == [incident_id]
    assert await pending(redis_client) == 0


async def test_restart_after_a_crash_escalates_agent_interrupted(
    pool: asyncpg.Pool, redis_client: Redis
) -> None:
    incident_id = await incident(pool)
    await pool.execute(
        "insert into ocp.evidence (incident_id, ref, kind, purpose, tool_name, summary, payload)"
        " values ($1, 'E2', 'log_query', 'gather', 'query_logs', 'x', '{}')",
        incident_id,
    )
    await Queues(redis_client).enqueue_agent_job(incident_id)
    crashing = Recorder(fail=True)
    worker = consumer(redis_client, pool, crashing)
    await worker.ensure_group()
    await worker.poll(100)
    await worker.drain()
    assert await pending(redis_client) == 1  # never acknowledged

    restarted = consumer(redis_client, pool, Recorder())  # same consumer name
    assert await restarted.recover_own_pending() == 1
    row = await pool.fetchrow(
        "select status::text, status_reason from ocp.incidents where id = $1", incident_id
    )
    assert row is not None and tuple(row) == ("escalated", "agent_interrupted")
    assert (
        await pool.fetchval(
            "select count(*) from ocp.audit_log"
            " where incident_id = $1 and event = 'agent.interrupted'",
            incident_id,
        )
        == 1
    )
    refs = [
        r["ref"]
        for r in await pool.fetch(
            "select ref from ocp.evidence where incident_id = $1", incident_id
        )
    ]
    assert sorted(refs) == ["E1", "E2"]  # kept, no duplicates
    assert await pending(redis_client) == 0
    assert crashing.runs == [incident_id]  # never investigated twice


async def test_autoclaim_takes_over_a_dead_workers_job(
    pool: asyncpg.Pool, redis_client: Redis
) -> None:
    incident_id = await incident(pool)
    await Queues(redis_client).enqueue_agent_job(incident_id)
    dead = consumer(redis_client, pool, Recorder(fail=True), "dead")
    await dead.ensure_group()
    await dead.poll(100)
    await dead.drain()
    other = consumer(redis_client, pool, Recorder(), "w2", idle_ms=1)
    await asyncio.sleep(0.01)
    assert await other.autoclaim() == 1
    status = await pool.fetchval(
        "select status_reason from ocp.incidents where id = $1", incident_id
    )
    assert status == "agent_interrupted"


async def test_a_running_job_is_not_taken_over_before_300_s(
    pool: asyncpg.Pool, redis_client: Redis
) -> None:
    incident_id = await incident(pool)
    await Queues(redis_client).enqueue_agent_job(incident_id)
    slow = consumer(redis_client, pool, Recorder(delay=0.2), "w1")
    await slow.ensure_group()
    await slow.poll(100)
    other = consumer(redis_client, pool, Recorder(), "w2")  # default 300 s
    assert await other.autoclaim() == 0
    await slow.drain()


async def test_redelivery_for_a_finished_run_is_only_acknowledged(
    pool: asyncpg.Pool, redis_client: Redis
) -> None:
    incident_id = await incident(pool)
    await pool.execute(
        "update ocp.incidents set status = 'escalated', status_reason = 'low_confidence',"
        " agent_meta = jsonb_build_object('run_started_at', '2026-10-21T10:01:00Z') where id = $1",
        incident_id,
    )
    version = await pool.fetchval(
        "select state_version from ocp.incidents where id = $1", incident_id
    )
    await Queues(redis_client).enqueue_agent_job(incident_id)
    run = Recorder()
    c = consumer(redis_client, pool, run)
    await c.ensure_group()
    await c.poll(100)
    await c.drain()
    assert run.runs == []
    assert (
        await pool.fetchval("select state_version from ocp.incidents where id = $1", incident_id)
        == version
    )
    assert await pending(redis_client) == 0


async def test_malformed_and_unknown_jobs_are_acknowledged(
    pool: asyncpg.Pool, redis_client: Redis
) -> None:
    await redis_client.xadd(AGENT_JOBS_STREAM, {"incident_id": "nope"})
    await redis_client.xadd(AGENT_JOBS_STREAM, {"incident_id": str(uuid.uuid4())})
    run = Recorder()
    c = consumer(redis_client, pool, run)
    await c.ensure_group()
    await c.poll(100)
    await c.drain()
    assert run.runs == [] and await pending(redis_client) == 0


async def test_concurrency_is_capped(pool: asyncpg.Pool, redis_client: Redis) -> None:
    services = ["api", "worker", "lb"]
    ids = [await incident(pool, s) for s in services]
    for incident_id in ids:
        await Queues(redis_client).enqueue_agent_job(incident_id)
    run = Recorder(delay=0.2)
    c = consumer(redis_client, pool, run)
    await c.ensure_group()
    assert await c.poll(100) == 2  # AGENT_CONCURRENCY
    assert await c.poll(10) == 0  # full
    await c.drain()
    assert await c.poll(100) == 1
    await c.drain()
    assert sorted(run.runs) == sorted(ids)
