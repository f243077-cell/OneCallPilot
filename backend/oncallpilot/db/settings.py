"""``monitor_settings``: one row, optimistic locking on ``version`` (FR-023, DB-010)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from oncallpilot.db.pool import DbConn


async def current(conn: DbConn, lock: bool = False) -> Any:
    return await conn.fetchrow(
        "select version, settings, updated_by, updated_at from ocp.monitor_settings"
        " where id = 1" + (" for update" if lock else "")
    )


async def save(
    conn: DbConn, version: int, settings: dict[str, Any], user_id: UUID, now: datetime
) -> None:
    """Insert the first row or replace the current one."""
    await conn.execute(
        "insert into ocp.monitor_settings (id, version, settings, updated_by, updated_at)"
        " values (1, $1, $2, $3, $4) on conflict (id) do update set version = excluded.version,"
        " settings = excluded.settings, updated_by = excluded.updated_by,"
        " updated_at = excluded.updated_at",
        version,
        settings,
        user_id,
        now,
    )
