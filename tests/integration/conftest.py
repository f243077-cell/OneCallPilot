"""Fixtures for the integration suite: backend-api against a real Postgres.

``OCP_TEST_DATABASE_URL`` names a superuser DSN to a ``pgvector/pgvector``
container (never Supabase). The session builds one migrated template
database; every test gets its own copy of it, so tests never share rows and
the append-only ``audit_log`` never needs cleaning. Redis is a fake for the
REST tests and a real throwaway one (``OCP_TEST_REDIS_URL``) for the event
tests; the JWKS is local; CI never calls Supabase (``CLAUDE.md`` §7).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import asyncpg
import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis

from oncallpilot.api.app import IngestConfig, Services, create_app
from oncallpilot.api.auth import JwksCache, TokenVerifier
from oncallpilot.db.incidents import IncidentRepository
from oncallpilot.db.pool import create_pool
from oncallpilot.events import EventStream
from oncallpilot.queues import Queues
from tests.keys import ISSUER, FakeJwks, SigningKey

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _dsn(base: str, name: str) -> str:
    return f"{base.rpartition('/')[0]}/{name}"


async def _admin(sql: str) -> None:
    conn = await asyncpg.connect(os.environ["OCP_TEST_DATABASE_URL"], timeout=10)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _build_template(name: str) -> None:
    await _admin(f'create database "{name}"')
    conn = await asyncpg.connect(_dsn(os.environ["OCP_TEST_DATABASE_URL"], name), timeout=10)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def template_db() -> Iterator[str]:
    if not os.environ.get("OCP_TEST_DATABASE_URL"):
        pytest.skip("OCP_TEST_DATABASE_URL is not set (needs a pgvector/pgvector container)")
    name = f"ocp_tpl_{uuid.uuid4().hex[:12]}"
    asyncio.run(_build_template(name))
    try:
        yield name
    finally:
        asyncio.run(_admin(f'drop database if exists "{name}" with (force)'))


@pytest.fixture
async def database_url(template_db: str) -> AsyncIterator[str]:
    """DSN of a fresh copy of the migrated template, dropped after the test."""
    name = f"ocp_it_{uuid.uuid4().hex[:12]}"
    await _admin(f'create database "{name}" template "{template_db}"')
    try:
        yield _dsn(os.environ["OCP_TEST_DATABASE_URL"], name)
    finally:
        await _admin(f'drop database if exists "{name}" with (force)')


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[asyncpg.Pool]:
    created = await create_pool(database_url)
    try:
        yield created
    finally:
        await created.close()


class FakeRedis:
    def __init__(self) -> None:
        self.up = True

    async def ping(self) -> bool:
        return self.up


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    """A real Redis (``OCP_TEST_REDIS_URL``, a throwaway container), flushed per test."""
    url = os.environ.get("OCP_TEST_REDIS_URL")
    if not url:
        pytest.skip("OCP_TEST_REDIS_URL is not set (needs a throwaway redis container)")
    client: Redis = Redis.from_url(url, decode_responses=True, socket_timeout=5)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@dataclass
class Api:
    client: httpx.AsyncClient
    key: SigningKey
    redis: FakeRedis

    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key.token()}"}


INGEST_TOKEN = "test-ingest-token"
TEST_INGEST = IngestConfig(SecretStr(INGEST_TOKEN), True, b"detector: test\n")


def build_services(
    pool: asyncpg.Pool, key: SigningKey, redis: Redis | FakeRedis, ingest: IngestConfig
) -> Services:
    real = redis if isinstance(redis, Redis) else None
    return Services(
        pool=pool,
        redis=redis,
        verifier=TokenVerifier(JwksCache(FakeJwks(key)), ISSUER),
        incidents=IncidentRepository(pool),
        events=EventStream(real) if real else cast(Any, None),
        queues=Queues(real) if real else cast(Any, None),
        ingest=ingest,
    )


@asynccontextmanager
async def serve(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Services]:
        yield services

    app = create_app(factory)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
    ):
        yield client


@pytest.fixture
async def api(pool: asyncpg.Pool) -> AsyncIterator[Api]:
    """backend-api on the test database with a fake Redis (REST reads)."""
    key = SigningKey("it-key")
    redis = FakeRedis()
    async with serve(build_services(pool, key, redis, TEST_INGEST)) as client:
        yield Api(client=client, key=key, redis=redis)


@dataclass
class LiveApi:
    client: httpx.AsyncClient
    redis: Redis
    pool: asyncpg.Pool


@pytest.fixture
async def live_api(pool: asyncpg.Pool, redis_client: Redis) -> AsyncIterator[LiveApi]:
    """backend-api on the test database and a real Redis (ingest, events, jobs)."""
    async with serve(build_services(pool, SigningKey("it-key"), redis_client, TEST_INGEST)) as c:
        yield LiveApi(client=c, redis=redis_client, pool=pool)


# The approval fixture lives with its helpers; importing it here shares it.
from approval_env import env  # noqa: E402, F401
