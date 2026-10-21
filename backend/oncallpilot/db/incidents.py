"""Incident reads for ``GET /incidents`` and ``GET /incidents/{id}`` (API-004, API-005).

Rows are mapped to the contract models of C1, so a response that leaves this
module is already valid against ``contracts/schemas``. Evidence is referred to
by its ``ref`` (``E1``…), never by UUID (§6.4).
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from oncallpilot_contracts.domain import (
    AgentMeta,
    Evidence,
    Execution,
    Hypothesis,
    IncidentDetail,
    IncidentSummary,
    Proposal,
    TopHypothesis,
    Trigger,
)

MAX_PAGE = 100


class InvalidCursorError(ValueError):
    """The ``cursor`` query parameter was not produced by this API."""


@dataclass(frozen=True)
class PageCursor:
    opened_at: datetime
    id: UUID

    def encode(self) -> str:
        raw = json.dumps({"t": self.opened_at.isoformat(), "id": str(self.id)}).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def decode(value: str) -> PageCursor:
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            data = json.loads(raw)
            opened_at = datetime.fromisoformat(data["t"])
            if opened_at.tzinfo is None:
                raise ValueError("naive timestamp")
            return PageCursor(opened_at=opened_at, id=UUID(data["id"]))
        except (binascii.Error, ValueError, KeyError, TypeError) as exc:
            raise InvalidCursorError("invalid cursor") from exc


_SUMMARY_SELECT = """
select i.id, i.service, i.severity::text as severity, i.status::text as status,
       i.status_reason, i.title, i.opened_at, i.resolved_at, i.resolution::text as resolution,
       i.state_version, {detail_columns}
       h.summary as h_summary, h.confidence as h_confidence,
       h.root_cause_category as h_category,
       (select p.id from ocp.proposals p
         where p.incident_id = i.id and p.status = 'pending') as pending_proposal_id
from ocp.incidents i
left join lateral (
  select summary, confidence, root_cause_category from ocp.hypotheses
  where incident_id = i.id and status = 'active'
  order by rank limit 1
) h on true
"""


def _summary_fields(row: asyncpg.Record) -> dict[str, Any]:
    top = None
    if row["h_summary"] is not None:
        top = TopHypothesis(
            summary=row["h_summary"],
            confidence=float(row["h_confidence"]),
            root_cause_category=row["h_category"],
        )
    return {
        "id": row["id"],
        "service": row["service"],
        "severity": row["severity"],
        "status": row["status"],
        "status_reason": row["status_reason"],
        "title": row["title"],
        "opened_at": row["opened_at"],
        "resolved_at": row["resolved_at"],
        "resolution": row["resolution"],
        "state_version": row["state_version"],
        "top_hypothesis": top,
        "pending_proposal_id": row["pending_proposal_id"],
    }


class IncidentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def service_of(self, incident_id: UUID) -> str | None:
        service: str | None = await self._pool.fetchval(
            "select service from ocp.incidents where id = $1", incident_id
        )
        return service

    async def get_summary(self, incident_id: UUID) -> IncidentSummary | None:
        row = await self._pool.fetchrow(
            _SUMMARY_SELECT.format(detail_columns="") + " where i.id = $1", incident_id
        )
        return None if row is None else IncidentSummary(**_summary_fields(row))

    async def list_page(
        self,
        *,
        status: str | None,
        service: str | None,
        limit: int,
        cursor: PageCursor | None,
    ) -> tuple[list[IncidentSummary], PageCursor | None]:
        """One page, newest first; keyset pagination on ``(opened_at, id)``."""
        limit = max(1, min(limit, MAX_PAGE))
        rows = await self._pool.fetch(
            _SUMMARY_SELECT.format(detail_columns="")
            + """
where ($1::text is null or i.status::text = $1)
  and ($2::text is null or i.service = $2)
  and ($3::timestamptz is null or (i.opened_at, i.id) < ($3, $4::uuid))
order by i.opened_at desc, i.id desc
limit $5
""",
            status,
            service,
            cursor.opened_at if cursor else None,
            cursor.id if cursor else None,
            limit + 1,
        )
        items = [IncidentSummary(**_summary_fields(row)) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = PageCursor(opened_at=last["opened_at"], id=last["id"])
        return items, next_cursor

    async def get_detail(self, incident_id: UUID) -> IncidentDetail | None:
        async with self._pool.acquire() as conn, conn.transaction(isolation="repeatable_read"):
            row = await conn.fetchrow(
                _SUMMARY_SELECT.format(
                    detail_columns="i.window_start, i.window_end, i.trigger, i.agent_meta,"
                )
                + "where i.id = $1",
                incident_id,
            )
            if row is None:
                return None
            evidence_rows = await conn.fetch(
                "select id, ref, kind::text as kind, purpose::text as purpose, tool_name,"
                " summary, payload, suspicious_content, created_at"
                " from ocp.evidence where incident_id = $1"
                " order by substring(ref from 2)::int",
                incident_id,
            )
            hypothesis_rows = await conn.fetch(
                "select id, rank, summary, root_cause_category, confidence, confidence_initial,"
                " evidence_ids, status, reflection_notes"
                " from ocp.hypotheses where incident_id = $1"
                " order by status = 'dropped', rank, created_at",
                incident_id,
            )
            proposal_rows = await conn.fetch(
                "select id, incident_id, kind::text as kind, parent_proposal_id, action, params,"
                " risk_tier::text as risk_tier, approval_requirement, expected_effect, rationale,"
                " evidence_ids, rollback_step, dry_run_result, dry_run_at, fingerprint,"
                " status::text as status, status_reason, expires_at, created_at"
                " from ocp.proposals where incident_id = $1 order by created_at, id",
                incident_id,
            )
            execution_rows = await conn.fetch(
                "select e.id, e.proposal_id, e.status::text as status, e.output, e.error_code,"
                " e.health_after, e.started_at, e.finished_at"
                " from ocp.executions e join ocp.proposals p on p.id = e.proposal_id"
                " where p.incident_id = $1 order by e.created_at, e.id",
                incident_id,
            )

        ref_of = {r["id"]: r["ref"] for r in evidence_rows}

        def refs(ids: list[UUID]) -> list[str]:
            return [ref_of[i] for i in ids if i in ref_of]

        evidence = [Evidence(**dict(r)) for r in evidence_rows]
        hypotheses = [
            Hypothesis(
                id=r["id"],
                rank=r["rank"],
                summary=r["summary"],
                root_cause_category=r["root_cause_category"],
                confidence=float(r["confidence"]),
                confidence_initial=float(r["confidence_initial"]),
                evidence_refs=refs(r["evidence_ids"]),
                status=r["status"],
                reflection_notes=r["reflection_notes"],
            )
            for r in hypothesis_rows
        ]
        proposals = []
        for r in proposal_rows:
            fields = dict(r)
            fields["evidence_refs"] = refs(fields.pop("evidence_ids"))
            proposals.append(Proposal(**fields))
        executions = []
        for r in execution_rows:
            fields = dict(r)
            output = fields.pop("output") or {}
            fields["steps"] = output.get("steps", [])
            executions.append(Execution(**fields))

        return IncidentDetail(
            **_summary_fields(row),
            window_start=row["window_start"],
            window_end=row["window_end"],
            trigger=Trigger.model_validate(row["trigger"]),
            evidence=evidence,
            hypotheses=hypotheses,
            proposals=proposals,
            executions=executions,
            agent_meta=AgentMeta.model_validate(row["agent_meta"]),
        )
