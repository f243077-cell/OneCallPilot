"""backend-api routes with fake services: auth, error envelope, health (API-001, API-002)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

import httpx
import pytest
from oncallpilot_contracts.api import ErrorEnvelope, Healthz, IncidentPage
from oncallpilot_contracts.domain import IncidentSummary
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError

from oncallpilot.api.app import IngestConfig, Services, create_app
from oncallpilot.api.auth import JwksCache, TokenVerifier
from oncallpilot.db.incidents import PageCursor
from tests.keys import ISSUER, FakeJwks, SigningKey

TEST_INGEST = IngestConfig(SecretStr("test-ingest-token"), True, b"")
KEY = SigningKey("k1")


class FakePool:
    def __init__(self) -> None:
        self.up = True

    async def fetchval(self, query: str, timeout: float | None = None) -> int:
        if not self.up:
            raise OSError("connection refused")
        return 1


class FakeRedis:
    def __init__(self) -> None:
        self.up = True

    async def ping(self) -> bool:
        if not self.up:
            raise RedisConnectionError("down")
        return True


def summary(**overrides: Any) -> IncidentSummary:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "service": "api",
        "severity": "sev2",
        "status": "investigating",
        "status_reason": None,
        "title": "error rate high on api",
        "opened_at": datetime(2026, 10, 21, 10, tzinfo=UTC),
        "resolved_at": None,
        "resolution": None,
        "state_version": 1,
        "top_hypothesis": None,
        "pending_proposal_id": None,
    }
    fields.update(overrides)
    return IncidentSummary(**fields)


class FakeIncidents:
    def __init__(self) -> None:
        self.items = [summary()]
        self.calls: list[dict[str, Any]] = []
        self.explode = False

    async def list_page(self, **kwargs: Any) -> tuple[list[IncidentSummary], PageCursor | None]:
        if self.explode:
            raise RuntimeError("secret detail that must not leak")
        self.calls.append(kwargs)
        last = self.items[-1]
        return self.items, PageCursor(opened_at=last.opened_at, id=last.id)

    async def get_detail(self, incident_id: uuid.UUID) -> None:
        return None


class Env:
    def __init__(self) -> None:
        self.pool = FakePool()
        self.redis = FakeRedis()
        self.incidents = FakeIncidents()
        self.services = Services(
            pool=cast(Any, self.pool),
            redis=self.redis,
            verifier=TokenVerifier(JwksCache(FakeJwks(KEY)), ISSUER),
            incidents=cast(Any, self.incidents),
            events=cast(Any, None),
            queues=cast(Any, None),
            ingest=TEST_INGEST,
        )


@pytest.fixture
async def env() -> AsyncIterator[tuple[Env, httpx.AsyncClient]]:
    built = Env()

    @asynccontextmanager
    async def services() -> AsyncIterator[Services]:
        yield built.services

    app = create_app(services)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield built, client


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {KEY.token()}"}


def assert_envelope(response: httpx.Response, status: int, code: str) -> ErrorEnvelope:
    assert response.status_code == status, response.text
    body = ErrorEnvelope.model_validate(response.json())
    assert body.error.code == code
    return body


# --- API-002 healthz ----------------------------------------------------------


async def test_healthz_ok_without_auth(env: tuple[Env, httpx.AsyncClient]) -> None:
    _, client = env
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = Healthz.model_validate(response.json())
    assert (body.status, body.db, body.redis) == ("ok", "up", "up")
    assert body.contracts_version == "0.1.0"


async def test_healthz_503_when_redis_is_down(env: tuple[Env, httpx.AsyncClient]) -> None:
    built, client = env
    built.redis.up = False
    response = await client.get("/healthz")
    assert response.status_code == 503
    body = Healthz.model_validate(response.json())
    assert (body.status, body.db, body.redis) == ("degraded", "up", "down")


async def test_healthz_503_when_the_database_is_down(env: tuple[Env, httpx.AsyncClient]) -> None:
    built, client = env
    built.pool.up = False
    response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["db"] == "down"


# --- Auth (SEC-010) and the error envelope (API-001) ---------------------------


async def test_incidents_need_a_bearer_token(env: tuple[Env, httpx.AsyncClient]) -> None:
    _, client = env
    response = await client.get("/incidents")
    assert_envelope(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert_envelope(await client.get(f"/incidents/{uuid.uuid4()}"), 401, "UNAUTHORIZED")


@pytest.mark.parametrize("param", ["access_token", "token", "jwt"])
async def test_token_in_the_query_string_is_ignored(
    env: tuple[Env, httpx.AsyncClient], param: str
) -> None:
    _, client = env
    response = await client.get("/incidents", params={param: KEY.token()})
    assert_envelope(response, 401, "UNAUTHORIZED")


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Bearer   ", "Token x"])
async def test_malformed_authorization_header(
    env: tuple[Env, httpx.AsyncClient], header: str
) -> None:
    _, client = env
    response = await client.get("/incidents", headers={"Authorization": header})
    assert_envelope(response, 401, "UNAUTHORIZED")


async def test_expired_token_is_refused(env: tuple[Env, httpx.AsyncClient]) -> None:
    _, client = env
    headers = {"Authorization": f"Bearer {KEY.token(exp=1)}"}
    assert_envelope(await client.get("/incidents", headers=headers), 401, "UNAUTHORIZED")


async def test_list_incidents_passes_filters_and_returns_a_page(
    env: tuple[Env, httpx.AsyncClient],
) -> None:
    built, client = env
    response = await client.get(
        "/incidents",
        params={"status": "escalated", "service": "worker", "limit": 5},
        headers=auth(),
    )
    assert response.status_code == 200
    page = IncidentPage.model_validate(response.json())
    assert [item.id for item in page.items] == [built.incidents.items[0].id]
    assert page.next_cursor is not None
    assert built.incidents.calls == [
        {"status": "escalated", "service": "worker", "limit": 5, "cursor": None}
    ]
    response = await client.get("/incidents", params={"cursor": page.next_cursor}, headers=auth())
    assert response.status_code == 200
    assert built.incidents.calls[1]["cursor"] == PageCursor.decode(page.next_cursor)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"status": "closed"},
        {"service": "billing"},
        {"cursor": "not-a-cursor"},
        {"cursor": "x" * 501},
    ],
)
async def test_bad_query_parameters_give_validation_error(
    env: tuple[Env, httpx.AsyncClient], params: dict[str, Any]
) -> None:
    _, client = env
    response = await client.get("/incidents", params=params, headers=auth())
    body = assert_envelope(response, 422, "VALIDATION_ERROR")
    assert "errors" in body.error.details


async def test_unknown_incident_is_not_found(env: tuple[Env, httpx.AsyncClient]) -> None:
    _, client = env
    response = await client.get(f"/incidents/{uuid.uuid4()}", headers=auth())
    assert_envelope(response, 404, "NOT_FOUND")
    response = await client.get("/incidents/not-a-uuid", headers=auth())
    assert_envelope(response, 422, "VALIDATION_ERROR")


async def test_unknown_route_uses_the_envelope(env: tuple[Env, httpx.AsyncClient]) -> None:
    _, client = env
    assert_envelope(await client.get("/nope"), 404, "NOT_FOUND")


async def test_unhandled_error_is_internal_and_hides_the_cause(
    env: tuple[Env, httpx.AsyncClient],
) -> None:
    built, client = env
    built.incidents.explode = True
    response = await client.get("/incidents", headers=auth())
    assert_envelope(response, 500, "INTERNAL")
    assert "secret detail" not in response.text


async def test_cursor_round_trip() -> None:
    cursor = PageCursor(opened_at=datetime(2026, 10, 21, 10, 0, 1, 5, tzinfo=UTC), id=uuid.uuid4())
    assert PageCursor.decode(cursor.encode()) == cursor
