"""Execution rows and the worker's sweeps (architecture §5.10, §5.11; FR-013, FR-014, FR-024)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from oncallpilot.db.pool import DbConn

_EXECUTION = (
    "select e.id, e.status::text as status, e.output, e.health_after, e.started_at,"
    " e.finished_at, e.created_at, p.id as proposal_id, p.kind::text as kind,"
    " p.parent_proposal_id, p.action, p.params, p.rollback_step, p.risk_tier::text as risk_tier,"
    " p.status::text as proposal_status, i.id as incident_id, i.service,"
    " i.status::text as incident_status, i.severity::text as severity, i.trigger"
    " from ocp.executions e join ocp.proposals p on p.id = e.proposal_id"
    " join ocp.incidents i on i.id = p.incident_id where e.id = $1"
)


async def lock_execution(conn: DbConn, execution_id: UUID) -> Any:
    """The execution with its proposal and incident, all row-locked."""
    return await conn.fetchrow(_EXECUTION + " for update of e, p, i", execution_id)


async def update_execution(
    conn: DbConn,
    execution_id: UUID,
    *,
    status: str,
    output: dict[str, Any],
    error_code: str | None = None,
    health_after: dict[str, Any] | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> None:
    await conn.execute(
        "update ocp.executions set status = $2::text::ocp.execution_status, output = $3,"
        " error_code = coalesce($4, error_code), health_after = coalesce($5, health_after),"
        " started_at = coalesce(started_at, $6), finished_at = coalesce(finished_at, $7)"
        " where id = $1",
        execution_id,
        status,
        output,
        error_code,
        health_after,
        started_at,
        finished_at,
    )


async def set_health(
    conn: DbConn, execution_id: UUID, health_after: dict[str, Any], output: dict[str, Any]
) -> None:
    await conn.execute(
        "update ocp.executions set health_after = $2, output = $3 where id = $1",
        execution_id,
        health_after,
        output,
    )


async def timed_out(conn: DbConn, created_before: datetime) -> list[UUID]:
    """Executions still ``queued`` or ``running`` that were created before the cut-off."""
    rows = await conn.fetch(
        "select id from ocp.executions where status in ('queued', 'running')"
        " and created_at <= $1 order by created_at",
        created_before,
    )
    return [r["id"] for r in rows]


async def verifying(conn: DbConn) -> list[UUID]:
    """The latest succeeded execution of every incident in ``verifying``."""
    rows = await conn.fetch(
        "select distinct on (i.id) e.id from ocp.incidents i"
        " join ocp.proposals p on p.incident_id = i.id"
        " join ocp.executions e on e.proposal_id = p.id"
        " where i.status = 'verifying' and e.status = 'succeeded'"
        " order by i.id, e.finished_at desc"
    )
    return [r["id"] for r in rows]


async def expired_pending(conn: DbConn, now: datetime) -> list[UUID]:
    rows = await conn.fetch(
        "select id from ocp.proposals where status = 'pending' and expires_at <= $1"
        " order by expires_at",
        now,
    )
    return [r["id"] for r in rows]


async def lock_pending_proposal(conn: DbConn, proposal_id: UUID) -> Any:
    return await conn.fetchrow(
        "select p.id, p.status::text as status, p.expires_at, i.id as incident_id,"
        " i.status::text as incident_status, i.severity::text as severity"
        " from ocp.proposals p join ocp.incidents i on i.id = p.incident_id"
        " where p.id = $1 for update of p, i",
        proposal_id,
    )


async def resolve(conn: DbConn, incident_id: UUID, resolution: str, now: datetime) -> None:
    await conn.execute(
        "update ocp.incidents set status = 'resolved', status_reason = null,"
        " resolution = $2::text::ocp.resolution, resolved_at = $3, window_end = $3,"
        " updated_at = $3 where id = $1",
        incident_id,
        resolution,
        now,
    )
