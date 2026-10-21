"""/ws/incidents with an in-memory event stream (API-014…API-017, SEC-011)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from oncallpilot_contracts.ws import WsServerMessage
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from oncallpilot.api.app import Services, create_app
from oncallpilot.api.auth import JwksCache, TokenVerifier
from oncallpilot.api.routers.ws import WsTimings
from oncallpilot.events import parse_id
from tests.keys import ISSUER, FakeJwks, SigningKey

KEY = SigningKey("k1")
TIMINGS = WsTimings(auth_timeout=0.3, ping_interval=0.25, max_missed_pongs=2, read_block_ms=20)
INCIDENT = uuid.uuid4()


def entry(n: int) -> dict[str, Any]:
    """A valid incident.updated envelope (without type and event_id)."""
    return {
        "event": "incident.updated",
        "incident_id": str(INCIDENT),
        "state_version": n + 1,
        "ts": "2026-10-21T10:00:00Z",
        "data": {"status": "investigating", "status_reason": None, "severity": "sev2"},
    }


class MemoryStream:
    """Stands in for EventStream: entries with ids "<n>-0"; oldest kept is ``first``."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []
        self.first = 1

    def add(self, count: int = 1) -> None:
        for _ in range(count):
            n = len(self.entries) + 1
            self.entries.append((f"{n}-0", entry(n)))

    async def latest_id(self) -> str | None:
        return self.entries[-1][0] if self.entries else None

    async def read_after(self, after: str, block_ms: int | None, count: int = 100) -> list[Any]:
        deadline = time.monotonic() + (block_ms or 0) / 1000
        while True:
            found = [
                {"type": "event", "event_id": i, **body}
                for i, body in self.entries
                if parse_id(i) > parse_id(after) and parse_id(i) >= (self.first, 0)
            ][:count]
            if found or time.monotonic() >= deadline:
                return found
            await asyncio.sleep(0.005)

    async def resumable_from(self, last_event_id: str) -> bool:
        try:
            last = parse_id(last_event_id)
        except ValueError:
            return False
        if not self.entries:
            return False
        return (self.first, 0) <= last <= parse_id(self.entries[-1][0])


class FakeRedis:
    async def ping(self) -> bool:
        return True


@pytest.fixture
def stream() -> MemoryStream:
    return MemoryStream()


@pytest.fixture
def client(stream: MemoryStream) -> Iterator[TestClient]:
    services = Services(
        pool=cast(Any, None),
        redis=FakeRedis(),
        verifier=TokenVerifier(JwksCache(FakeJwks(KEY)), ISSUER, leeway_seconds=0),
        incidents=cast(Any, None),
        events=cast(Any, stream),
        ws_timings=TIMINGS,
    )

    @asynccontextmanager
    async def factory() -> AsyncIterator[Services]:
        yield services

    with TestClient(create_app(factory)) as test_client:
        yield test_client


def close_code(ws: WebSocketTestSession) -> int:
    """Read until the server closes; return the close code."""
    with pytest.raises(WebSocketDisconnect) as closed:
        while True:
            ws.receive_json()
    return closed.value.code


def receive(ws: WebSocketTestSession, kind: str | None = None) -> dict[str, Any]:
    """Next message (skipping pings unless asked for), validated against C3."""
    while True:
        message: dict[str, Any] = ws.receive_json()
        WsServerMessage.model_validate(message)
        if kind == "ping" or message["type"] != "ping":
            return message


@contextmanager
def authed(client: TestClient, token: str | None = None) -> Iterator[WebSocketTestSession]:
    with client.websocket_connect("/ws/incidents") as session:
        session.send_json({"type": "auth", "token": token or KEY.token()})
        yield session


# --- API-014 authentication -----------------------------------------------------


def test_no_auth_within_the_timeout_closes_4408(client: TestClient) -> None:
    with client.websocket_connect("/ws/incidents") as ws:
        assert close_code(ws) == 4408


def test_token_in_the_url_is_ignored(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/incidents?token={KEY.token()}") as ws:
        assert close_code(ws) == 4408


@pytest.mark.parametrize(
    "first",
    [
        {"type": "auth", "token": "garbage"},
        {"type": "auth", "token": KEY.token(exp=int(time.time()) - 10)},
        {"type": "auth", "token": SigningKey("k1").token()},
        {"type": "pong"},
        {"type": "resume", "last_event_id": "1-0"},
    ],
)
def test_bad_first_message_closes_4401(client: TestClient, first: dict[str, Any]) -> None:
    with client.websocket_connect("/ws/incidents") as ws:
        ws.send_json(first)
        assert close_code(ws) == 4401


def test_hello_after_auth(client: TestClient, stream: MemoryStream) -> None:
    stream.add(3)
    with authed(client) as session:
        hello = receive(session)
        assert hello["type"] == "hello"
        assert hello["latest_event_id"] == "3-0"
        assert hello["contracts_version"] == "0.1.0"


def test_hello_on_an_empty_stream(client: TestClient) -> None:
    with authed(client) as session:
        assert receive(session)["latest_event_id"] is None


def test_expired_token_without_refresh_closes_4401(client: TestClient) -> None:
    with authed(client, KEY.token(exp=int(time.time()) + 1)) as session:
        receive(session)
        started = time.monotonic()
        while True:
            try:
                session.receive_json()
            except WebSocketDisconnect as closed:
                assert closed.code == 4401
                break
            session.send_json({"type": "pong"})
        assert time.monotonic() - started < 3


def test_refresh_extends_the_session(client: TestClient) -> None:
    user = str(uuid.uuid4())
    with authed(client, KEY.token(sub=user, exp=int(time.time()) + 1)) as session:
        receive(session)
        session.send_json({"type": "auth", "token": KEY.token(sub=user)})
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            message = session.receive_json()  # raises if the socket is closed
            if message["type"] == "ping":
                session.send_json({"type": "pong"})


def test_refresh_for_another_user_closes_4401(client: TestClient) -> None:
    with authed(client) as session:
        receive(session)
        session.send_json({"type": "auth", "token": KEY.token()})  # new random sub
        assert close_code(session) == 4401


# --- API-015 events -----------------------------------------------------------


def test_live_events_are_forwarded_in_order(client: TestClient, stream: MemoryStream) -> None:
    stream.add(2)
    with authed(client) as session:
        receive(session)
        stream.add(3)
        ids = [receive(session)["event_id"] for _ in range(3)]
        assert ids == ["3-0", "4-0", "5-0"]


# --- API-016 resume and resync --------------------------------------------------


def test_resume_replays_exactly_the_missed_events(client: TestClient, stream: MemoryStream) -> None:
    stream.add(5)
    stream.add(10)  # missed while disconnected
    with authed(client) as session:
        session.send_json({"type": "resume", "last_event_id": "5-0"})
        assert receive(session)["type"] == "hello"
        events = [receive(session) for _ in range(10)]
        assert [e["event_id"] for e in events] == [f"{n}-0" for n in range(6, 16)]
        stream.add(1)
        assert receive(session)["event_id"] == "16-0"


@pytest.mark.parametrize("last", ["0-0", "2-0", "99-0"])
def test_resume_too_old_or_unknown_needs_resync(
    client: TestClient, stream: MemoryStream, last: str
) -> None:
    stream.add(10)
    stream.first = 5  # 1-4 trimmed
    with authed(client) as session:
        receive(session)
        session.send_json({"type": "resume", "last_event_id": last})
        assert receive(session)["type"] == "resync_required"


# --- API-017 heartbeat ----------------------------------------------------------


def test_ping_and_pong_keep_the_connection(client: TestClient) -> None:
    with authed(client) as session:
        receive(session)
        for _ in range(4):
            assert receive(session, "ping")["type"] == "ping"
            session.send_json({"type": "pong"})


def test_two_missed_pongs_close_the_connection(client: TestClient) -> None:
    with authed(client) as session:
        receive(session)
        started = time.monotonic()
        pings = 0
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                if session.receive_json()["type"] == "ping":
                    pings += 1
        assert closed.value.code == 1008
        assert pings == 2
        assert time.monotonic() - started < 3 * TIMINGS.ping_interval + 0.5


def test_unknown_messages_are_ignored(client: TestClient, stream: MemoryStream) -> None:
    with authed(client) as session:
        receive(session)
        session.send_text("not json")
        session.send_json({"type": "subscribe", "x": 1})
        stream.add(1)
        assert receive(session)["event_id"] == "1-0"


def test_event_fixture_bodies_are_valid() -> None:
    for n in range(1, 3):
        WsServerMessage.model_validate({"type": "event", "event_id": f"{n}-0", **entry(n)})
    assert json.dumps(entry(1))
