"""The asyncpg pool releases without the reset query (``db/pool.py``; DB-014, B3.4)."""

from __future__ import annotations

import asyncpg
import pytest
from asyncpg.connection import LoggedQuery


async def test_release_sends_no_query_when_no_transaction_is_open(pool: asyncpg.Pool) -> None:
    sent: list[str] = []

    def log(q: LoggedQuery) -> None:
        sent.append(q.query)

    async with pool.acquire() as conn:
        raw = conn._con  # the connection under the proxy keeps the logger after release
        raw.add_query_logger(log)
        await conn.execute("select 1")
    raw.remove_query_logger(log)

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
