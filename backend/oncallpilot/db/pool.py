"""asyncpg pool (DB-014). SQL lives only in ``oncallpilot.db`` (``CLAUDE.md`` §4.4).

Sessions run in UTC so every timestamp comes back with a zero offset, and
``jsonb`` values are decoded to Python objects.
"""

from __future__ import annotations

import json

import asyncpg
from asyncpg.pool import PoolConnectionProxy

DbConn = asyncpg.Connection | PoolConnectionProxy
"""A plain connection or one acquired from a pool."""

CONNECT_TIMEOUT_SECONDS = 10.0
COMMAND_TIMEOUT_SECONDS = 10.0


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def _keep_session(conn: asyncpg.Connection) -> None:
    """Release without asyncpg's reset query (``RESET ALL``, ``UNLISTEN *``, …).

    That query costs one database round trip on every release: about 0.3–0.7 s
    to Supabase ``ocp-dev`` (Sydney), 20–30 times per investigation
    (``docs/checks/latency.md``). This code keeps no session state: no
    ``LISTEN``, advisory locks, cursors or session-level ``SET`` (only
    ``SET LOCAL``, which ends with its transaction). asyncpg still rolls back a
    transaction left open before it calls this.
    """


async def create_pool(dsn: str, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        timeout=CONNECT_TIMEOUT_SECONDS,
        command_timeout=COMMAND_TIMEOUT_SECONDS,
        server_settings={"timezone": "UTC", "application_name": "oncallpilot"},
        init=_init_connection,
        reset=_keep_session,
    )


async def ping(pool: asyncpg.Pool, timeout: float) -> bool:
    try:
        return bool(await pool.fetchval("select 1", timeout=timeout))
    except (OSError, TimeoutError, asyncpg.PostgresError, asyncpg.InterfaceError):
        return False
