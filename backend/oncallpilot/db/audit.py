"""Audit log reads (API-012). Rows are append-only and stored already redacted."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from oncallpilot.db.pool import DbConn


async def page(
    conn: DbConn, incident_id: UUID | None, before_id: int | None, limit: int
) -> list[Any]:
    """Newest first; keyset on ``id``."""
    return list(
        await conn.fetch(
            "select id, actor, event, ref_type, ref_id, incident_id, payload, created_at"
            " from ocp.audit_log"
            " where ($1::uuid is null or incident_id = $1)"
            " and ($2::bigint is null or id < $2)"
            " order by id desc limit $3",
            incident_id,
            before_id,
            limit,
        )
    )
