"""Escalating an incident (architecture §5.7; FR-009, FR-010).

One place for the status change, its audit row(s), and the
``incident.updated`` event, used by the agent and by the worker (for example
``agent_interrupted``). Escalating never overrides a status that FR-009 does
not allow to move to ``escalated`` (such as ``resolved``).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from oncallpilot_contracts.enums import EscalationReason
from redis.exceptions import RedisError

from oncallpilot.db import agent as db
from oncallpilot.events import EventStream
from oncallpilot.transitions import IllegalStatusChangeError, check_transition

log = logging.getLogger(__name__)


async def escalate_incident(
    pool: asyncpg.Pool,
    events: EventStream,
    incident_id: UUID,
    reason: EscalationReason,
    now: datetime,
    actor: str = "agent",
    extra_audit: tuple[str, dict[str, Any]] | None = None,
) -> bool:
    """Returns False (and changes nothing) when the incident may not be escalated now."""
    async with pool.acquire() as conn, conn.transaction():
        row = await db.lock_incident(conn, incident_id)
        try:
            check_transition(row["status"], "escalated")
        except IllegalStatusChangeError:
            log.warning(
                "not escalating: incident is %s", row["status"], extra={"incident_id": incident_id}
            )
            return False
        severity = await conn.fetchval(
            "select severity::text from ocp.incidents where id = $1", incident_id
        )
        await db.set_status(conn, incident_id, "escalated", reason, now)
        version = await db.bump(conn, incident_id, now)
        if extra_audit is not None:
            await db.audit(
                conn, actor, extra_audit[0], incident_id, "incident", incident_id, extra_audit[1]
            )
        await db.audit(
            conn, actor, "incident.status_changed", incident_id, "incident", incident_id,
            {"from": row["status"], "to": "escalated", "reason": reason},
        )  # fmt: skip
    try:
        await events.publish(
            "incident.updated",
            incident_id,
            version,
            now,
            {"status": "escalated", "status_reason": reason, "severity": severity},
        )
    except RedisError as exc:
        log.error(
            "publish incident.updated failed: %s",
            type(exc).__name__,
            extra={"incident_id": incident_id},
        )
    log.info("incident escalated: %s", reason, extra={"incident_id": incident_id})
    return True
