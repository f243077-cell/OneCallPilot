"""Write a fixture timeline's final ``IncidentDetail`` into the database (phases.md §4, B1.4).

Used by ``ocp seed-incident`` and by the integration tests, never by the
agent. Everything is inserted in one transaction with the ids, refs and
timestamps of the fixture, so ``GET /incidents/{id}`` returns the fixture
back. Rows the contract does not carry are filled in plainly:

- every execution gets an ``approved`` approval (the database refuses an
  execution without one, DB-007), made by ``SEED_USER_ID``;
- a rejected proposal gets a ``rejected`` approval with its status reason;
- ``detector_config_version`` is ``seed``, and so is a proposal's
  ``catalogue_version`` when ``agent_meta`` has none.

Each step writes an ``audit_log`` row with actor ``system``.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from oncallpilot_contracts.domain import IncidentDetail

from oncallpilot.db.pool import DbConn

SEED_USER_ID = UUID("00000000-0000-4000-8000-000000005eed")
DETECTOR_CONFIG_VERSION = "seed"


async def write_incident_detail(conn: DbConn, detail: IncidentDetail) -> None:
    """Insert ``detail`` and its children. Raises if the incident id already exists."""
    async with conn.transaction():
        await _write(conn, detail)


async def _write(conn: DbConn, d: IncidentDetail) -> None:
    await conn.execute(
        "insert into ocp.incidents (id, service, severity, status, status_reason, title,"
        " trigger, opened_at, resolved_at, resolution, window_start, window_end,"
        " state_version, detector_config_version, agent_meta, created_at, updated_at)"
        " values ($1, $2, $3::text::ocp.severity, $4::text::ocp.incident_status, $5, $6, $7,"
        " $8, $9, $10::text::ocp.resolution, $11, $12, $13, $14, $15, $8, now())",
        d.id,
        d.service,
        d.severity,
        d.status,
        d.status_reason,
        d.title,
        d.trigger.model_dump(mode="json"),
        d.opened_at,
        d.resolved_at,
        d.resolution,
        d.window_start,
        d.window_end,
        d.state_version,
        DETECTOR_CONFIG_VERSION,
        d.agent_meta.model_dump(mode="json", exclude_none=True),
    )
    await _audit(conn, d.id, "incident.opened", "incident", d.id)

    id_of_ref: dict[str, UUID] = {}
    for e in d.evidence:
        id_of_ref[e.ref] = e.id
        await conn.execute(
            "insert into ocp.evidence (id, incident_id, ref, kind, purpose, tool_name, summary,"
            " payload, suspicious_content, created_at)"
            " values ($1, $2, $3, $4::text::ocp.evidence_kind,"
            " $5::text::ocp.evidence_purpose, $6, $7, $8, $9, $10)",
            e.id,
            d.id,
            e.ref,
            e.kind,
            e.purpose,
            e.tool_name,
            e.summary,
            e.payload,
            e.suspicious_content,
            e.created_at,
        )
        await _audit(conn, d.id, "evidence.added", "evidence", e.id, {"ref": e.ref})

    for h in d.hypotheses:
        await conn.execute(
            "insert into ocp.hypotheses (id, incident_id, rank, summary, root_cause_category,"
            " confidence, confidence_initial, evidence_ids, status, reflection_notes)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            h.id,
            d.id,
            h.rank,
            h.summary,
            h.root_cause_category,
            h.confidence,
            h.confidence_initial,
            [id_of_ref[ref] for ref in h.evidence_refs],
            h.status,
            h.reflection_notes,
        )
        await _audit(conn, d.id, "hypothesis.created", "hypothesis", h.id, {"rank": h.rank})

    executed = {x.proposal_id: x for x in d.executions}
    for p in sorted(d.proposals, key=lambda p: (p.parent_proposal_id is not None, p.created_at)):
        await conn.execute(
            "insert into ocp.proposals (id, incident_id, kind, parent_proposal_id, action,"
            " params, risk_tier, approval_requirement, expected_effect, rationale, evidence_ids,"
            " rollback_step, dry_run_result, dry_run_at, fingerprint, status, status_reason,"
            " expires_at, catalogue_version, created_at, updated_at)"
            " values ($1, $2, $3::text::ocp.proposal_kind, $4, $5, $6,"
            " $7::text::ocp.risk_tier, $8, $9, $10, $11, $12, $13, $14, $15,"
            " $16::text::ocp.proposal_status, $17, $18, $19, $20, now())",
            p.id,
            d.id,
            p.kind,
            p.parent_proposal_id,
            p.action,
            p.params,
            p.risk_tier,
            p.approval_requirement,
            p.expected_effect,
            p.rationale,
            [id_of_ref[ref] for ref in p.evidence_refs],
            p.rollback_step.model_dump(mode="json") if p.rollback_step else None,
            p.dry_run_result.model_dump(mode="json"),
            p.dry_run_at,
            p.fingerprint,
            p.status,
            p.status_reason,
            p.expires_at,
            d.agent_meta.catalogue_version or DETECTOR_CONFIG_VERSION,
            p.created_at,
        )
        await _audit(conn, d.id, "proposal.created", "proposal", p.id, {"action": p.action})

        execution = executed.get(p.id)
        if execution is not None:
            approval_id = await _approval(conn, p.id, "approved", p.approval_requirement, None)
            await conn.execute(
                "insert into ocp.executions (id, proposal_id, approval_id, status, output,"
                " error_code, health_after, started_at, finished_at)"
                " values ($1, $2, $3, $4::text::ocp.execution_status, $5, $6, $7, $8, $9)",
                execution.id,
                p.id,
                approval_id,
                execution.status,
                {"steps": [step.model_dump(mode="json") for step in execution.steps]},
                execution.error_code,
                execution.health_after.model_dump(mode="json") if execution.health_after else None,
                execution.started_at,
                execution.finished_at,
            )
            await _audit(conn, d.id, "execution.queued", "execution", execution.id)
        elif p.status == "rejected":
            await _approval(
                conn, p.id, "rejected", p.approval_requirement, p.status_reason or "rejected"
            )


async def _approval(
    conn: DbConn, proposal_id: UUID, decision: str, method: str, reason: str | None
) -> UUID:
    approval_id = uuid.uuid4()
    await conn.execute(
        "insert into ocp.approvals (id, proposal_id, user_id, decision, reason, auth_method,"
        " challenge_id, idempotency_key)"
        " values ($1, $2, $3, $4::text::ocp.decision, $5, $6, $7, $8)",
        approval_id,
        proposal_id,
        SEED_USER_ID,
        decision,
        reason,
        method,
        uuid.uuid4() if decision == "approved" else None,
        f"seed:{approval_id}",
    )
    return approval_id


async def _audit(
    conn: DbConn,
    incident_id: UUID,
    event: str,
    ref_type: str,
    ref_id: UUID,
    payload: dict[str, object] | None = None,
) -> None:
    await conn.execute(
        "insert into ocp.audit_log (actor, event, ref_type, ref_id, incident_id, payload)"
        " values ('system', $1, $2, $3, $4, $5)",
        event,
        ref_type,
        ref_id,
        incident_id,
        {"seeded": True, **(payload or {})},
    )
