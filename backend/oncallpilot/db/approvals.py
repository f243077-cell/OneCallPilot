"""Approval and execution rows (architecture §5.9 step 5, §8.2; DB-006, DB-007)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from oncallpilot.db.pool import DbConn

_PROPOSAL = (
    "select p.id, p.incident_id, p.kind::text as kind, p.action, p.params,"
    " p.risk_tier::text as risk_tier, p.approval_requirement, p.fingerprint,"
    " p.status::text as status, p.expires_at, p.dry_run_result, p.evidence_ids,"
    " p.catalogue_version, i.service, i.status::text as incident_status,"
    " i.severity::text as severity"
    " from ocp.proposals p join ocp.incidents i on i.id = p.incident_id where p.id = $1"
)


async def get_proposal(conn: DbConn, proposal_id: UUID) -> Any:
    return await conn.fetchrow(_PROPOSAL, proposal_id)


async def lock_proposal(conn: DbConn, proposal_id: UUID) -> Any:
    """The proposal and its incident, both row-locked until the transaction ends."""
    return await conn.fetchrow(_PROPOSAL + " for update of p, i", proposal_id)


async def device_of_user(conn: DbConn, device_id: UUID, user_id: UUID) -> UUID | None:
    found: UUID | None = await conn.fetchval(
        "select id from ocp.devices where id = $1 and user_id = $2", device_id, user_id
    )
    return found


async def insert_approval(
    conn: DbConn,
    *,
    proposal_id: UUID,
    user_id: UUID,
    decision: str,
    reason: str | None,
    auth_method: str,
    challenge_id: UUID | None,
    device_id: UUID | None,
    idempotency_key: UUID,
    now: datetime,
) -> UUID:
    approval_id: UUID = await conn.fetchval(
        "insert into ocp.approvals (proposal_id, user_id, decision, reason, auth_method,"
        " challenge_id, device_id, idempotency_key, decided_at)"
        " values ($1, $2, $3::text::ocp.decision, $4, $5, $6, $7, $8, $9) returning id",
        proposal_id,
        user_id,
        decision,
        reason,
        auth_method,
        challenge_id,
        device_id,
        str(idempotency_key),
        now,
    )
    return approval_id


async def insert_execution(
    conn: DbConn, proposal_id: UUID, approval_id: UUID, now: datetime
) -> UUID:
    execution_id: UUID = await conn.fetchval(
        "insert into ocp.executions (proposal_id, approval_id, status, created_at)"
        " values ($1, $2, 'queued', $3) returning id",
        proposal_id,
        approval_id,
        now,
    )
    return execution_id


async def set_proposal_status(
    conn: DbConn, proposal_id: UUID, status: str, reason: str | None, now: datetime
) -> None:
    await conn.execute(
        "update ocp.proposals set status = $2::text::ocp.proposal_status, status_reason = $3,"
        " updated_at = $4 where id = $1",
        proposal_id,
        status,
        reason,
        now,
    )


async def top_hypothesis(conn: DbConn, incident_id: UUID) -> Any:
    return await conn.fetchrow(
        "select summary, confidence from ocp.hypotheses"
        " where incident_id = $1 and status = 'active' and rank = 1",
        incident_id,
    )


async def evidence_refs(conn: DbConn, evidence_ids: list[UUID]) -> list[str]:
    rows = await conn.fetch(
        "select ref from ocp.evidence where id = any($1::uuid[])"
        " order by substring(ref from 2)::int",
        evidence_ids,
    )
    return [r["ref"] for r in rows]
