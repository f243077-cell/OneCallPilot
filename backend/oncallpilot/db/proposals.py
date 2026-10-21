"""Proposal rows (architecture §8.2 ``proposals``; DB-005)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from oncallpilot.db.pool import DbConn


async def top_hypothesis_id(conn: DbConn, incident_id: UUID) -> UUID | None:
    hypothesis_id: UUID | None = await conn.fetchval(
        "select id from ocp.hypotheses where incident_id = $1 and status = 'active' and rank = 1",
        incident_id,
    )
    return hypothesis_id


async def insert_pending(
    conn: DbConn,
    *,
    proposal_id: UUID,
    incident_id: UUID,
    hypothesis_id: UUID | None,
    action: str,
    params: dict[str, Any],
    risk_tier: str,
    approval_requirement: str,
    expected_effect: str,
    rationale: str,
    evidence_ids: list[UUID],
    rollback_step: dict[str, Any] | None,
    dry_run_result: dict[str, Any],
    dry_run_at: datetime,
    fingerprint: str,
    expires_at: datetime,
    catalogue_version: str,
    now: datetime,
) -> None:
    await conn.execute(
        "insert into ocp.proposals (id, incident_id, hypothesis_id, kind, action, params,"
        " risk_tier, approval_requirement, expected_effect, rationale, evidence_ids,"
        " rollback_step, dry_run_result, dry_run_at, fingerprint, status, expires_at,"
        " catalogue_version, created_at, updated_at)"
        " values ($1, $2, $3, 'primary', $4, $5, $6::text::ocp.risk_tier, $7, $8, $9, $10,"
        " $11, $12, $13, $14, 'pending', $15, $16, $17, $17)",
        proposal_id,
        incident_id,
        hypothesis_id,
        action,
        params,
        risk_tier,
        approval_requirement,
        expected_effect,
        rationale,
        evidence_ids,
        rollback_step,
        dry_run_result,
        dry_run_at,
        fingerprint,
        expires_at,
        catalogue_version,
        now,
    )
