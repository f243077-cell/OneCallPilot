"""Resolving an incident outside an action's verification (architecture §5.11; FR-011, FR-012).

One place for the auto-resolver (``auto_recovered``) and the manual resolve
endpoint (``manual``): in one transaction the incident becomes ``resolved``,
a pending proposal is closed as ``expired``, and the audit rows are written;
then ``proposal.updated`` and ``incident.resolved`` are published. Nothing is
resolved while an execution is queued or running.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg

from oncallpilot.db import agent as incident_db
from oncallpilot.db import approvals as approvals_db
from oncallpilot.db import executions as db
from oncallpilot.events import EventStream
from oncallpilot.proposal_store import iso, publish
from oncallpilot.transitions import IllegalStatusChangeError, check_transition

log = logging.getLogger(__name__)

Outcome = Literal["resolved", "already_resolved", "busy", "not_allowed"]


async def resolve_incident(
    pool: asyncpg.Pool,
    events: EventStream,
    incident_id: UUID,
    resolution: Literal["auto_recovered", "manual"],
    actor: str,
    now: datetime,
    reason: str | None = None,
) -> Outcome:
    """Raises ``LookupError`` for an unknown incident."""
    published: list[tuple[str, int, dict[str, Any]]] = []
    async with pool.acquire() as conn, conn.transaction():
        row = await incident_db.lock_incident(conn, incident_id)
        if row["status"] == "resolved":
            return "already_resolved"
        if await db.execution_in_progress(conn, incident_id):
            return "busy"
        try:
            check_transition(row["status"], "resolved")
        except IllegalStatusChangeError:
            return "not_allowed"
        pending = await db.pending_of(conn, incident_id)
        if pending is not None:
            await approvals_db.set_proposal_status(
                conn, pending, "expired", "incident resolved", now
            )
            version = await incident_db.bump(conn, incident_id, now)
            await incident_db.audit(
                conn, actor, "proposal.expired", incident_id, "proposal", pending,
                {"reason": "incident resolved"},
            )  # fmt: skip
            published.append(
                ("proposal.updated", version,
                 {"proposal_id": str(pending), "status": "expired",
                  "status_reason": "incident resolved", "superseded_by": None})
            )  # fmt: skip
        await db.resolve(conn, incident_id, resolution, now)
        version = await incident_db.bump(conn, incident_id, now)
        payload: dict[str, Any] = {"resolution": resolution, "from": row["status"]}
        if reason is not None:
            payload["reason"] = reason
        await incident_db.audit(
            conn, actor, "incident.resolved", incident_id, "incident", incident_id, payload
        )
        published.append(
            ("incident.resolved", version, {"resolution": resolution, "resolved_at": iso(now)})
        )
    for event, version, data in published:
        await publish(events, event, incident_id, version, now, data)
    log.info("incident resolved: %s", resolution, extra={"incident_id": incident_id})
    return "resolved"
