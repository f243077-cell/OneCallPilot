"""The asyncpg pool releases without the reset query (``db/pool.py``; DB-014, B3.4)."""

from __future__ import annotations

import asyncpg
import pytest
from asyncpg.connection import LoggedQuery

from oncallpilot.db.pool import create_pool


async def test_release_sends_no_query_when_no_transaction_is_open(database_url: str) -> None:
    sent: list[str] = []

    def log(q: LoggedQuery) -> None:
        sent.append(q.query)

    single = await create_pool(database_url, max_size=1)  # the same connection comes back
    try:
        async with single.acquire() as conn:
            conn.add_query_logger(log)
            await conn.execute("select 1")
        async with single.acquire() as conn:
            conn.remove_query_logger(log)
    finally:
        await single.close()

    assert sent == ["select 1"]


async def test_release_still_rolls_back_a_transaction_left_open(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        await conn.execute("create table ocp_pool_probe (x int)")
        # released without commit or rollback

    async with pool.acquire() as conn:
        assert not conn.is_in_transaction()
        exists = await conn.fetchval("select to_regclass('public.ocp_pool_probe') is not null")
    assert exists is False


@pytest.mark.parametrize("_round", range(2))
async def test_sessions_stay_in_utc_across_releases(pool: asyncpg.Pool, _round: int) -> None:
    async with pool.acquire() as conn:
        assert await conn.fetchval("show timezone") == "UTC"
