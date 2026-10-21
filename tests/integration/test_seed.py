"""``python -m oncallpilot.seed`` (B1.4): a timeline into the database and ocp:events."""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest
from oncallpilot_contracts.timeline import Timeline
from redis.asyncio import Redis

from oncallpilot.db.incidents import IncidentRepository
from oncallpilot.events import EventStream
from oncallpilot.seed import main, seed, with_new_ids

TIMELINES = sorted(
    (Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "timelines").glob("*.json")
)


def load(path: Path) -> Timeline:
    return Timeline.model_validate_json(path.read_text())


@pytest.mark.parametrize("path", TIMELINES, ids=lambda p: p.stem)
async def test_seed_writes_final_and_publishes_every_event(
    database_url: str, pool: asyncpg.Pool, redis_client: Redis, path: Path
) -> None:
    timeline = load(path)
    redis_url = os.environ["OCP_TEST_REDIS_URL"]
    event_ids = await seed(timeline, database_url, redis_url, realtime=False)
    assert len(event_ids) == len(timeline.steps)
    assert await IncidentRepository(pool).get_detail(timeline.final.id) == timeline.final
    published = await EventStream(redis_client).read_after("0-0", block_ms=None, count=1000)
    assert [e["event_id"] for e in published] == event_ids
    assert [(e["event"], e["state_version"]) for e in published] == [
        (s.event.event, s.event.state_version) for s in timeline.steps
    ]


def test_new_ids_replace_every_own_id_consistently() -> None:
    timeline = load(TIMELINES[0])
    fresh = with_new_ids(timeline)
    assert fresh.final.id != timeline.final.id
    assert fresh.incident_id == fresh.final.id
    assert {e.id for e in fresh.final.evidence}.isdisjoint({e.id for e in timeline.final.evidence})
    assert all(step.event.incident_id == fresh.final.id for step in fresh.steps)
    # Everything else (refs, text, timestamps) is unchanged.
    assert [e.ref for e in fresh.final.evidence] == [e.ref for e in timeline.final.evidence]
    assert fresh.final.title == timeline.final.title


async def test_same_timeline_twice_with_new_ids(
    database_url: str, pool: asyncpg.Pool, redis_client: Redis
) -> None:
    resolved = next(p for p in TIMELINES if load(p).final.status == "resolved")
    redis_url = os.environ["OCP_TEST_REDIS_URL"]
    first = with_new_ids(load(resolved))
    second = with_new_ids(load(resolved))
    await seed(first, database_url, redis_url, realtime=False)
    await seed(second, database_url, redis_url, realtime=False)
    assert await pool.fetchval("select count(*) from ocp.incidents") == 2
    with pytest.raises(asyncpg.UniqueViolationError):
        await seed(first, database_url, redis_url, realtime=False)


def test_cli_needs_both_urls(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    assert main([str(TIMELINES[0])]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
