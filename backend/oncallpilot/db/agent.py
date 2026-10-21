"""Writes of the agent (architecture §5.2–§5.7; AI-010, AI-012, AI-024).

Each function runs in its caller's transaction and bumps the incident's
``state_version`` once, so the caller can publish exactly one event carrying
the new value after commit (FR-019). Every change writes an audit row in the
same transaction (``CLAUDE.md`` §2.11).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from oncallpilot.db.pool import DbConn


async def lock_incident(conn: DbConn, incident_id: UUID) -> Any:
    row = await conn.fetchrow(
        "select id, service, title, status::text as status, window_start, window_end,"
        " state_version, agent_meta from ocp.incidents where id = $1 for update",
        incident_id,
    )
    if row is None:
        raise LookupError(f"incident {incident_id} not found")
    return row


async def bump(conn: DbConn, incident_id: UUID, now: datetime) -> int:
    version: int = await conn.fetchval(
        "update ocp.incidents set state_version = state_version + 1, updated_at = $2"
        " where id = $1 returning state_version",
        incident_id,
        now,
    )
    return version


async def audit(
    conn: DbConn,
    actor: str,
    event: str,
    incident_id: UUID,
    ref_type: str | None = None,
    ref_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        "insert into ocp.audit_log (actor, event, ref_type, ref_id, incident_id, payload)"
        " values ($1, $2, $3, $4, $5, $6)",
        actor,
        event,
        ref_type,
        ref_id,
        incident_id,
        payload or {},
    )


async def evidence_rows(conn: DbConn, incident_id: UUID) -> list[Any]:
    return list(
        await conn.fetch(
            "select id, ref, kind::text as kind, purpose::text as purpose, tool_name, summary,"
            " payload, suspicious_content, created_at from ocp.evidence where incident_id = $1"
            " order by substring(ref from 2)::int",
            incident_id,
        )
    )


async def next_ref(conn: DbConn, incident_id: UUID) -> str:
    last = await conn.fetchval(
        "select max(substring(ref from 2)::int) from ocp.evidence where incident_id = $1",
        incident_id,
    )
    return f"E{(last or 0) + 1}"


async def insert_evidence(
    conn: DbConn,
    incident_id: UUID,
    ref: str,
    kind: str,
    purpose: str,
    tool_name: str,
    params: dict[str, Any],
    summary: str,
    payload: dict[str, Any],
    suspicious: bool,
    now: datetime,
) -> UUID:
    evidence_id = uuid.uuid4()
    await conn.execute(
        "insert into ocp.evidence (id, incident_id, ref, kind, purpose, tool_name, params,"
        " summary, payload, suspicious_content, created_at)"
        " values ($1, $2, $3, $4::text::ocp.evidence_kind, $5::text::ocp.evidence_purpose,"
        " $6, $7, $8, $9, $10, $11)",
        evidence_id,
        incident_id,
        ref,
        kind,
        purpose,
        tool_name,
        params,
        summary,
        payload,
        suspicious,
        now,
    )
    return evidence_id


async def insert_hypothesis(
    conn: DbConn,
    incident_id: UUID,
    rank: int,
    summary: str,
    category: str,
    confidence: float,
    evidence_ids: list[UUID],
    now: datetime,
) -> UUID:
    hypothesis_id = uuid.uuid4()
    await conn.execute(
        "insert into ocp.hypotheses (id, incident_id, rank, summary, root_cause_category,"
        " confidence, confidence_initial, evidence_ids, created_at, updated_at)"
        " values ($1, $2, $3, $4, $5, $6, $6, $7, $8, $8)",
        hypothesis_id,
        incident_id,
        rank,
        summary,
        category,
        round(confidence, 2),
        evidence_ids,
        now,
    )
    return hypothesis_id


async def update_hypothesis(
    conn: DbConn,
    hypothesis_id: UUID,
    rank: int,
    confidence: float,
    dropped: bool,
    notes: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        "update ocp.hypotheses set rank = $2, confidence = $3, status = $4,"
        " reflection_notes = $5, updated_at = $6 where id = $1",
        hypothesis_id,
        rank,
        round(confidence, 2),
        "dropped" if dropped else "active",
        notes,
        now,
    )


async def park_ranks(conn: DbConn, incident_id: UUID) -> None:
    """Mark every hypothesis dropped first, so re-ranking never hits the active-rank index."""
    await conn.execute(
        "update ocp.hypotheses set status = 'dropped' where incident_id = $1", incident_id
    )


async def all_hypotheses(conn: DbConn, incident_id: UUID) -> list[Any]:
    return list(
        await conn.fetch(
            "select id, rank, summary, root_cause_category, confidence, confidence_initial,"
            " evidence_ids, status, reflection_notes from ocp.hypotheses"
            " where incident_id = $1 order by status = 'dropped', rank, created_at",
            incident_id,
        )
    )


async def set_status(
    conn: DbConn, incident_id: UUID, status: str, reason: str | None, now: datetime
) -> None:
    await conn.execute(
        "update ocp.incidents set status = $2::text::ocp.incident_status, status_reason = $3,"
        " updated_at = $4 where id = $1",
        incident_id,
        status,
        reason,
        now,
    )


async def merge_agent_meta(conn: DbConn, incident_id: UUID, values: dict[str, Any]) -> None:
    await conn.execute(
        "update ocp.incidents set agent_meta = agent_meta || $2 where id = $1",
        incident_id,
        values,
    )


async def claim_for_investigation(conn: DbConn, incident_id: UUID, meta: dict[str, Any]) -> str:
    """Claim an incident for one investigation (architecture §5.2; AI-025).

    One short transaction: ``FOR UPDATE SKIP LOCKED``, then ``run_started_at``
    (plus ``meta``) is written and committed at once, so the row lock is not
    held during the run. Returns:

    - ``run``: claimed; investigate it;
    - ``busy``: another worker holds the row right now; leave the job pending;
    - ``interrupted``: a run had started and the incident is still investigating;
    - ``done``: a run had started and finished (any other status), or no such incident.
    """
    async with conn.transaction():
        row = await conn.fetchrow(
            "select status::text as status, agent_meta from ocp.incidents"
            " where id = $1 for update skip locked",
            incident_id,
        )
        if row is None:
            exists = await conn.fetchval(
                "select exists (select 1 from ocp.incidents where id = $1)", incident_id
            )
            return "busy" if exists else "done"
        if (row["agent_meta"] or {}).get("run_started_at"):
            return "interrupted" if row["status"] == "investigating" else "done"
        if row["status"] != "investigating":
            return "done"
        await conn.execute(
            "update ocp.incidents set agent_meta = agent_meta || $2 where id = $1",
            incident_id,
            meta,
        )
        return "run"
