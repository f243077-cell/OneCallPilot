"""Storing a pending proposal (architecture §5.6 step 6, §5.10 step 2, §5.11; FR-015, FR-017).

One place for the agent's proposal, a rollback proposal after a failed
action, and the re-proposal after ``STATE_DRIFT``: in one transaction the
pending row (with the C8 fingerprint), the incident → ``awaiting_approval``,
and the audit rows; then ``proposal.updated`` for a superseded proposal,
``proposal.created`` and ``incident.updated``, on consecutive state versions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import asyncpg
from redis.exceptions import RedisError

from oncallpilot.agent.proposer import PreparedProposal, proposal_fingerprint
from oncallpilot.db import agent as db
from oncallpilot.db import approvals as approvals_db
from oncallpilot.db import proposals as proposals_db
from oncallpilot.events import EventStream
from oncallpilot.transitions import IllegalStatusChangeError, check_transition

log = logging.getLogger(__name__)

PROPOSAL_TTL_SECONDS = 900


def iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ProposalText:
    """What the engineer reads, and what the proposal cites."""

    expected_effect: str
    rationale: str
    evidence_ids: list[UUID]
    evidence_refs: list[str]
    hypothesis_id: UUID | None


async def store_proposal(
    pool: asyncpg.Pool,
    events: EventStream,
    incident_id: UUID,
    prepared: PreparedProposal,
    text: ProposalText,
    now: datetime,
    *,
    kind: Literal["primary", "rollback"] = "primary",
    parent_id: UUID | None = None,
    supersedes: UUID | None = None,
    ttl_seconds: int = PROPOSAL_TTL_SECONDS,
    actor: str = "agent",
) -> bool:
    """False (and nothing stored) when the incident may not await approval now."""
    p = prepared
    expires_at = now + timedelta(seconds=ttl_seconds)
    fingerprint = proposal_fingerprint(
        p.proposal_id, p.action, p.params, p.risk_tier, p.dry_run.state_fingerprint, p.dry_run_at
    )
    dry_run = p.dry_run.model_dump(mode="json")
    async with pool.acquire() as conn, conn.transaction():
        row = await db.lock_incident(conn, incident_id)
        try:
            check_transition(row["status"], "awaiting_approval")
        except IllegalStatusChangeError:
            log.warning(
                "proposal not stored: incident is %s", row["status"],
                extra={"incident_id": incident_id},
            )  # fmt: skip
            return False
        severity = await conn.fetchval(
            "select severity::text from ocp.incidents where id = $1", incident_id
        )
        await proposals_db.insert_pending(
            conn,
            proposal_id=p.proposal_id,
            incident_id=incident_id,
            hypothesis_id=text.hypothesis_id,
            kind=kind,
            parent_proposal_id=parent_id,
            action=p.action,
            params=p.params,
            risk_tier=p.risk_tier,
            approval_requirement=p.approval_requirement,
            expected_effect=text.expected_effect,
            rationale=text.rationale,
            evidence_ids=text.evidence_ids,
            rollback_step=p.rollback_step,
            dry_run_result=dry_run,
            dry_run_at=p.dry_run_at,
            fingerprint=fingerprint,
            expires_at=expires_at,
            catalogue_version=p.catalogue_version,
            now=now,
        )
        superseded_version = None
        if supersedes is not None:
            await proposals_db.supersede(conn, supersedes, p.proposal_id, now)
            superseded_version = await db.bump(conn, incident_id, now)
            await db.audit(
                conn, actor, "proposal.superseded", incident_id, "proposal", supersedes,
                {"superseded_by": str(p.proposal_id)},
            )  # fmt: skip
        created_version = await db.bump(conn, incident_id, now)
        await db.set_status(conn, incident_id, "awaiting_approval", None, now)
        updated_version = await db.bump(conn, incident_id, now)
        await db.audit(
            conn, actor, "proposal.created", incident_id, "proposal", p.proposal_id,
            {"kind": kind, "parent_proposal_id": str(parent_id) if parent_id else None,
             "action": p.action, "params": p.params, "risk_tier": p.risk_tier,
             "approval_requirement": p.approval_requirement, "fingerprint": fingerprint,
             "catalogue_version": p.catalogue_version, "refs": text.evidence_refs},
        )  # fmt: skip
        await db.audit(
            conn, actor, "incident.status_changed", incident_id, "incident", incident_id,
            {"from": row["status"], "to": "awaiting_approval"},
        )  # fmt: skip

    if superseded_version is not None and supersedes is not None:
        await publish(
            events, "proposal.updated", incident_id, superseded_version, now,
            {"proposal_id": str(supersedes), "status": "superseded", "status_reason": None,
             "superseded_by": str(p.proposal_id)},
        )  # fmt: skip
    await publish(
        events, "proposal.created", incident_id, created_version, now, {
            "id": str(p.proposal_id),
            "incident_id": str(incident_id),
            "kind": kind,
            "parent_proposal_id": str(parent_id) if parent_id else None,
            "action": p.action,
            "params": p.params,
            "risk_tier": p.risk_tier,
            "approval_requirement": p.approval_requirement,
            "expected_effect": text.expected_effect,
            "rationale": text.rationale,
            "evidence_refs": text.evidence_refs,
            "rollback_step": p.rollback_step,
            "dry_run_result": dry_run,
            "dry_run_at": iso(p.dry_run_at),
            "fingerprint": fingerprint,
            "status": "pending",
            "status_reason": None,
            "expires_at": iso(expires_at),
            "created_at": iso(now),
        },
    )  # fmt: skip
    await publish(
        events, "incident.updated", incident_id, updated_version, now,
        {"status": "awaiting_approval", "status_reason": None, "severity": severity},
    )  # fmt: skip
    log.info(
        "proposal stored: %s (%s)", p.action, kind,
        extra={"incident_id": incident_id, "proposal_id": p.proposal_id},
    )  # fmt: skip
    return True


async def text_from(conn: Any, proposal_id: UUID) -> ProposalText:
    """A proposal's own text and citations, for its re-proposal after drift."""
    row = await proposals_db.text_row(conn, proposal_id)
    return ProposalText(
        row["expected_effect"],
        row["rationale"],
        list(row["evidence_ids"]),
        await approvals_db.evidence_refs(conn, list(row["evidence_ids"])),
        row["hypothesis_id"],
    )


async def publish(
    events: EventStream,
    event: str,
    incident_id: UUID,
    version: int,
    ts: datetime,
    data: dict[str, Any],
) -> None:
    try:
        await events.publish(event, incident_id, version, ts, data)
    except RedisError as exc:
        # Committed already; the app still sees it through GET /incidents/{id}.
        log.error(
            "publish %s failed: %s", event, type(exc).__name__, extra={"incident_id": incident_id}
        )
