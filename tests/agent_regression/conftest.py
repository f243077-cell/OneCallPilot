"""Fixtures for the agent regression suite (TEST-002): a migrated throwaway Postgres
(``OCP_TEST_DATABASE_URL``) and Redis (``OCP_TEST_REDIS_URL``), as in tests/integration.
CI never calls an LLM, Supabase or FCM (``CLAUDE.md`` §7)."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
from redis.asyncio import Redis

from oncallpilot.db.pool import create_pool

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
# The FakeRunnerConsumer lives with the integration suite.
sys.path.insert(0, str(ROOT / "tests" / "integration"))


def _dsn(name: str) -> str:
    return f"{os.environ['OCP_TEST_DATABASE_URL'].rpartition('/')[0]}/{name}"


async def _admin(sql: str) -> None:
    conn = await asyncpg.connect(os.environ["OCP_TEST_DATABASE_URL"], timeout=10)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _build_template(name: str) -> None:
    await _admin(f'create database "{name}"')
    conn = await asyncpg.connect(_dsn(name), timeout=10)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            await conn.execute(path.read_text(encoding="utf-8"))
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def template_db() -> Iterator[str]:
    if not os.environ.get("OCP_TEST_DATABASE_URL"):
        pytest.skip("OCP_TEST_DATABASE_URL is not set (needs a pgvector/pgvector container)")
    name = f"ocp_reg_tpl_{uuid.uuid4().hex[:12]}"
    asyncio.run(_build_template(name))
    try:
        yield name
    finally:
        asyncio.run(_admin(f'drop database if exists "{name}" with (force)'))


@pytest.fixture
async def pool(template_db: str) -> AsyncIterator[asyncpg.Pool]:
    name = f"ocp_reg_{uuid.uuid4().hex[:12]}"
    await _admin(f'create database "{name}" template "{template_db}"')
    created = await create_pool(_dsn(name))
    try:
        yield created
    finally:
        await created.close()
        await _admin(f'drop database if exists "{name}" with (force)')


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
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
