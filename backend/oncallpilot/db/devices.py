"""Devices and push settings (architecture §8.2 ``devices``, ``monitor_settings``; API-011)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from oncallpilot.db.pool import DbConn


async def incident_for_push(conn: DbConn, incident_id: UUID) -> Any:
    return await conn.fetchrow(
        "select severity::text as severity, service, title from ocp.incidents where id = $1",
        incident_id,
    )


async def min_push_severity(conn: DbConn) -> str:
    value: str | None = await conn.fetchval(
        "select settings -> 'notify' ->> 'min_push_severity' from ocp.monitor_settings where id = 1"
    )
    return value or "sev3"


async def enabled_devices(conn: DbConn) -> list[Any]:
    return list(
        await conn.fetch(
            "select id, fcm_token from ocp.devices where push_enabled order by created_at"
        )
    )


async def disable(conn: DbConn, device_id: UUID) -> None:
    await conn.execute("update ocp.devices set push_enabled = false where id = $1", device_id)


async def register(
    conn: DbConn, user_id: UUID, fcm_token: str, app_version: str, now: datetime
) -> UUID:
    """Upsert by token: the caller owns it from now on, and pushes are on."""
    device_id: UUID = await conn.fetchval(
        "insert into ocp.devices (user_id, fcm_token, platform, app_version, push_enabled,"
        " last_seen_at) values ($1, $2, 'android', $3, true, $4)"
        " on conflict (fcm_token) do update set user_id = excluded.user_id,"
        " app_version = excluded.app_version, push_enabled = true,"
        " last_seen_at = excluded.last_seen_at returning id",
        user_id,
        fcm_token,
        app_version,
        now,
    )
    return device_id


async def unregister(conn: DbConn, device_id: UUID, user_id: UUID) -> bool:
    """Sign-out: the owner's device stops receiving pushes (no DELETE grant, DB-011)."""
    found = await conn.fetchval(
        "update ocp.devices set push_enabled = false where id = $1 and user_id = $2 returning id",
        device_id,
        user_id,
    )
    return found is not None
