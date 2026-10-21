"""ocp:events and /ws/incidents against a real Redis (API-015, API-016, FR-019)."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from oncallpilot_contracts.ws import WsServerMessage
from pydantic import SecretStr, ValidationError
from redis.asyncio import Redis
from starlette.testclient import WebSocketTestSession

from oncallpilot.api.app import IngestConfig, Services, create_app
from oncallpilot.api.auth import JwksCache, TokenVerifier
from oncallpilot.api.routers.ws import WsTimings
from oncallpilot.events import EVENTS_STREAM, EventStream
from tests.keys import ISSUER, FakeJwks, SigningKey

WS_FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "ws"
EVENT_FIXTURES = sorted(p for p in WS_FIXTURES.glob("*.json") if p.stem.count(".") == 1)
TS = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)


def fixture_event(path: Path) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(path.read_text())
    return body


async def publish_fixture(events: EventStream, body: dict[str, Any]) -> str:
    return await events.publish(
        body["event"],
        uuid.UUID(body["incident_id"]),
        body["state_version"],
        datetime.fromisoformat(body["ts"]),
        body["data"],
    )


def test_every_event_type_has_a_fixture() -> None:
    assert {p.stem for p in EVENT_FIXTURES} == {
        "incident.opened",
        "incident.updated",
        "evidence.added",
        "hypothesis.updated",
        "proposal.created",
        "proposal.updated",
        "execution.progress",
        "incident.resolved",
    }


@pytest.mark.parametrize("path", EVENT_FIXTURES, ids=lambda p: p.stem)
async def test_fixture_events_round_trip(redis_client: Redis, path: Path) -> None:
    events = EventStream(redis_client)
    body = fixture_event(path)
    event_id = await publish_fixture(events, body)
    [read] = await events.read_after("0-0", block_ms=None)
    assert read == {**body, "event_id": event_id}
    WsServerMessage.model_validate(read)
    assert await events.latest_id() == event_id


async def test_publish_refuses_a_malformed_event(redis_client: Redis) -> None:
    events = EventStream(redis_client)
    with pytest.raises(ValidationError):
        await events.publish("incident.updated", uuid.uuid4(), 2, TS, {"status": "closed"})
    with pytest.raises(ValidationError):
        await events.publish("incident.deleted", uuid.uuid4(), 2, TS, {})
    assert await redis_client.exists(EVENTS_STREAM) == 0


def updated(n: int) -> dict[str, Any]:
    return {"status": "investigating", "status_reason": None, "severity": "sev2"}


async def test_resumable_window(redis_client: Redis) -> None:
    events = EventStream(redis_client, maxlen=5)
    assert not await events.resumable_from("1-0")  # no stream yet
    incident = uuid.uuid4()
    ids = [
        await events.publish("incident.updated", incident, n + 2, TS, updated(n)) for n in range(30)
    ]
    oldest: Any = await redis_client.xrange(EVENTS_STREAM, count=1)
    first_kept = str(oldest[0][0])
    assert first_kept != ids[0]  # trimmed (approximately) to about 5
    assert await events.resumable_from(first_kept)
    assert await events.resumable_from(ids[-1])
    assert not await events.resumable_from(ids[0])
    assert not await events.resumable_from("0-1")
    future = f"{int(ids[-1].split('-')[0]) + 10_000}-0"
    assert not await events.resumable_from(future)
    assert not await events.resumable_from("not-an-id")


async def test_read_after_blocks_until_a_new_event(redis_client: Redis) -> None:
    events = EventStream(redis_client)
    latest = await events.publish("incident.updated", uuid.uuid4(), 2, TS, updated(0))
    assert await events.read_after(latest, block_ms=50) == []

    async def later() -> str:
        await asyncio.sleep(0.1)
        return await events.publish("incident.updated", uuid.uuid4(), 3, TS, updated(1))

    publisher = asyncio.create_task(later())
    got = await events.read_after(latest, block_ms=2000)
    assert [e["event_id"] for e in got] == [await publisher]


# --- the gateway end to end ----------------------------------------------------

TEST_INGEST = IngestConfig(SecretStr("test-ingest-token"), True, b"")
KEY = SigningKey("it-ws")


@contextmanager
def gateway() -> Iterator[TestClient]:
    url = os.environ.get("OCP_TEST_REDIS_URL")
    if not url:
        pytest.skip("OCP_TEST_REDIS_URL is not set")

    @asynccontextmanager
    async def factory() -> AsyncIterator[Services]:
        redis: Redis = Redis.from_url(url, decode_responses=True, socket_timeout=5)
        try:
            yield Services(
                pool=cast(Any, None),
                redis=redis,
                verifier=TokenVerifier(JwksCache(FakeJwks(KEY)), ISSUER),
                incidents=cast(Any, None),
                events=EventStream(redis),
                queues=cast(Any, None),
                ingest=TEST_INGEST,
                ws_timings=WsTimings(read_block_ms=50),
            )
        finally:
            await redis.aclose()

    with TestClient(create_app(factory)) as client:
        yield client


def publish_many(count: int, start_version: int, incident: uuid.UUID) -> list[str]:
    async def run() -> list[str]:
        url = os.environ["OCP_TEST_REDIS_URL"]
        async with Redis.from_url(url, decode_responses=True) as redis:
            events = EventStream(redis)
            return [
                await events.publish(
                    "incident.updated", incident, start_version + i, TS, updated(i)
                )
                for i in range(count)
            ]

    return asyncio.run(run())


def events_until(session: WebSocketTestSession, count: int) -> list[dict[str, Any]]:
    got: list[dict[str, Any]] = []
    while len(got) < count:
        message: dict[str, Any] = session.receive_json()
        WsServerMessage.model_validate(message)
        if message["type"] == "event":
            got.append(message)
    return got


def test_disconnect_for_10_events_then_resume(redis_client: Redis) -> None:
    incident = uuid.uuid4()
    with gateway() as client:
        with client.websocket_connect("/ws/incidents") as session:
            session.send_json({"type": "auth", "token": KEY.token()})
            assert session.receive_json()["type"] == "hello"
            seen = publish_many(3, 2, incident)
            assert [e["event_id"] for e in events_until(session, 3)] == seen
        missed = publish_many(10, 5, incident)  # while disconnected
        with client.websocket_connect("/ws/incidents") as session:
            session.send_json({"type": "auth", "token": KEY.token()})
            session.send_json({"type": "resume", "last_event_id": seen[-1]})
            hello = session.receive_json()
            assert hello["latest_event_id"] == missed[-1]
            replayed = events_until(session, 10)
            assert [e["event_id"] for e in replayed] == missed
            assert [e["state_version"] for e in replayed] == list(range(5, 15))
            live = publish_many(1, 15, incident)
            assert [e["event_id"] for e in events_until(session, 1)] == live
