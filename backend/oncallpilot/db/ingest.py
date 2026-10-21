"""Alert ingestion writes (architecture §5.1; FR-004, FR-005, FR-007).

``ingest_alert`` runs in one transaction. Either it opens an incident with its
seed evidence ``E1`` and ``audit(incident.opened)``, or, when the service
already has an incident that is not resolved, it appends the alert to
``trigger.updates`` and adds seed evidence only for signal types that are new
to that incident (with ``audit(evidence.added)`` and a ``state_version`` bump).
Two alerts racing for the same service meet the partial unique index
``incidents_one_active_per_service``; the loser deduplicates.

Events and the agent job are the caller's job, after commit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import asyncpg
from oncallpilot_contracts.api import AlertIn
from oncallpilot_contracts.domain import (
    DetectorSignalPayload,
    Evidence,
    IncidentSummary,
    Signal,
    Trigger,
    TriggerUpdate,
)

from oncallpilot.db.pool import DbConn
from oncallpilot.ingest import seed_summary, severity, title, window_start


@dataclass(frozen=True)
class IngestOutcome:
    incident_id: UUID
    deduplicated: bool
    opened: IncidentSummary | None
    """The new incident (for ``incident.opened``), when one was opened."""
    added: Evidence | None
    """New seed evidence on a deduplicated incident (for ``evidence.added``)."""
    state_version: int


def _actor(alert: AlertIn) -> str:
    return "detector" if alert.source == "detector" else "system"


async def threshold_overrides(conn: DbConn) -> dict[str, float]:
    settings = await conn.fetchval("select settings from ocp.monitor_settings where id = 1")
    thresholds = (settings or {}).get("thresholds") or {}
    return {str(k): float(v) for k, v in thresholds.items()}


async def ingest_alert(
    conn: DbConn,
    alert: AlertIn,
    *,
    now: datetime,
    detector_config_version: str,
    benchmark_run_id: str | None,
) -> IngestOutcome:
    for _ in range(2):
        try:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    "select id, state_version, trigger from ocp.incidents"
                    " where service = $1 and status <> 'resolved' for update",
                    alert.service,
                )
                if existing is not None:
                    return await _deduplicate(conn, alert, existing, now)
                return await _open(conn, alert, now, detector_config_version, benchmark_run_id)
        except asyncpg.UniqueViolationError as exc:
            if getattr(exc, "constraint_name", None) != "incidents_one_active_per_service":
                raise
            continue  # another alert opened it first: deduplicate against it
    raise RuntimeError("could not open or find the active incident")  # pragma: no cover


async def _open(
    conn: DbConn,
    alert: AlertIn,
    now: datetime,
    detector_config_version: str,
    benchmark_run_id: str | None,
) -> IngestOutcome:
    incident_id = uuid.uuid4()
    trigger = Trigger(
        source=alert.source, signals=alert.signals, observed_at=alert.observed_at, updates=[]
    )
    summary = IncidentSummary(
        id=incident_id,
        service=alert.service,
        severity=severity(alert.signals),
        status="investigating",
        status_reason=None,
        title=title(alert.service, alert.signals),
        opened_at=now,
        resolved_at=None,
        resolution=None,
        state_version=1,
        top_hypothesis=None,
        pending_proposal_id=None,
    )
    await conn.execute(
        "insert into ocp.incidents (id, service, severity, status, title, trigger, opened_at,"
        " window_start, state_version, detector_config_version, benchmark_run_id,"
        " created_at, updated_at)"
        " values ($1, $2, $3::text::ocp.severity, 'investigating', $4, $5, $6, $7, 1, $8, $9,"
        " $6, $6)",
        incident_id,
        alert.service,
        summary.severity,
        summary.title,
        trigger.model_dump(mode="json"),
        now,
        window_start(alert.signals),
        detector_config_version,
        benchmark_run_id,
    )
    evidence = await _seed_evidence(conn, incident_id, "E1", alert, alert.signals, now)
    # E1 is part of opening: FR-004 expects exactly one audit row here.
    await _audit(
        conn,
        _actor(alert),
        "incident.opened",
        "incident",
        incident_id,
        incident_id,
        {
            "severity": summary.severity,
            "signals": [s.name for s in alert.signals],
            "seed": evidence.ref,
        },
    )
    return IngestOutcome(incident_id, False, summary, None, 1)


async def _deduplicate(
    conn: DbConn, alert: AlertIn, row: asyncpg.Record, now: datetime
) -> IngestOutcome:
    incident_id: UUID = row["id"]
    trigger = Trigger.model_validate(row["trigger"])
    update = TriggerUpdate(signals=alert.signals, observed_at=alert.observed_at)
    trigger = trigger.model_copy(update={"updates": [*trigger.updates, update]})

    seen = {s.name for s in trigger.signals}
    for earlier in trigger.updates[:-1]:
        seen |= {s.name for s in earlier.signals}
    new_signals = [s for s in alert.signals if s.name not in seen]

    state_version: int = row["state_version"]
    added: Evidence | None = None
    if new_signals:
        last = await conn.fetchval(
            "select max(substring(ref from 2)::int) from ocp.evidence where incident_id = $1",
            incident_id,
        )
        ref = f"E{(last or 0) + 1}"
        added = await _seed_evidence(conn, incident_id, ref, alert, new_signals, now)
        state_version += 1
        await _audit(
            conn, _actor(alert), "evidence.added", "evidence", added.id, incident_id, {"ref": ref}
        )
    await conn.execute(
        "update ocp.incidents set trigger = $2, state_version = $3, updated_at = $4 where id = $1",
        incident_id,
        trigger.model_dump(mode="json"),
        state_version,
        now,
    )
    return IngestOutcome(incident_id, True, None, added, state_version)


async def _seed_evidence(
    conn: DbConn,
    incident_id: UUID,
    ref: str,
    alert: AlertIn,
    signals: list[Signal],
    now: datetime,
) -> Evidence:
    payload = DetectorSignalPayload(
        service=alert.service, signals=signals, observed_at=alert.observed_at
    )
    evidence = Evidence(
        id=uuid.uuid4(),
        ref=ref,
        kind="detector_signal",
        purpose="seed",
        tool_name=None,
        summary=seed_summary(alert.service, signals),
        payload=payload.model_dump(mode="json"),
        suspicious_content=False,
        created_at=now,
    )
    await conn.execute(
        "insert into ocp.evidence (id, incident_id, ref, kind, purpose, summary, payload,"
        " created_at) values ($1, $2, $3, 'detector_signal', 'seed', $4, $5, $6)",
        evidence.id,
        incident_id,
        ref,
        evidence.summary,
        evidence.payload,
        now,
    )
    return evidence


async def _audit(
    conn: DbConn,
    actor: str,
    event: str,
    ref_type: str,
    ref_id: UUID,
    incident_id: UUID,
    payload: dict[str, object],
) -> None:
    await conn.execute(
        "insert into ocp.audit_log (actor, event, ref_type, ref_id, incident_id, payload)"
        " values ($1, $2, $3, $4, $5, $6)",
        actor,
        event,
        ref_type,
        ref_id,
        incident_id,
        payload,
    )
