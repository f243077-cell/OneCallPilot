"""``AgentEffects``: every side effect of an investigation (architecture §6.1; AI-001).

The state machine and the orchestrator never touch the database, Redis, the
tools or the runner directly; they call this interface. Tests replace it with
a fake; the real implementation stores evidence (redacted, with the next
``E#`` ref), hypotheses, proposals and audit rows, and publishes events.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from uuid import UUID

import asyncpg
from oncallpilot_contracts.enums import EscalationReason, EvidenceKind
from oncallpilot_contracts.ws import cut_evidence_payload
from redis.exceptions import RedisError

from oncallpilot.agent.proposer import PreparedProposal, Refusal
from oncallpilot.agent.providers.base import Usage
from oncallpilot.agent.redaction import looks_suspicious, redact, redact_payload
from oncallpilot.agent.schemas import HypothesisOut, ProposalOut
from oncallpilot.agent.summaries import summarise
from oncallpilot.agent.toolbox import ToolBox, ToolRefusedError
from oncallpilot.agent.untrusted import wrap
from oncallpilot.db import agent as db
from oncallpilot.db import proposals as proposals_db
from oncallpilot.escalation import escalate_incident
from oncallpilot.events import EventStream
from oncallpilot.proposal_store import PROPOSAL_TTL_SECONDS, ProposalText, store_proposal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvidenceView:
    """What the model may know about one evidence item: its ref, never its UUID."""

    ref: str
    kind: EvidenceKind
    summary: str
    rendered: str  # untrusted-wrapped, redacted content


@dataclass(frozen=True)
class IncidentContext:
    incident_id: UUID
    service: str
    title: str
    window_start: datetime
    window_end: datetime | None
    evidence: tuple[EvidenceView, ...]  # the seed evidence (E1, …)


@dataclass(frozen=True)
class ToolOutcome:
    """A tool call's result as the model sees it.

    ``evidence`` is None when the call was refused (bad parameters, unknown
    tool, backend error); ``message`` then explains why. Either way the call
    counts toward the budget.
    """

    evidence: EvidenceView | None
    message: str


@dataclass(frozen=True)
class RankedHypothesis:
    original_rank: int  # the rank it was stored with by store_hypotheses
    hypothesis: HypothesisOut
    confidence_initial: float
    confidence: float
    dropped: bool
    reflection_notes: str | None


ProposalResult = Literal["proposed", "invalid", "dry_run_unavailable"]


@dataclass(frozen=True)
class RunMeta:
    tool_calls: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    duration_ms: int
    terminal_state: Literal["PROPOSED", "ESCALATED"]


class AgentEffects(Protocol):
    async def load_context(self) -> IncidentContext: ...

    async def run_tool(
        self, name: str, arguments: dict[str, Any], purpose: Literal["gather", "disproof"]
    ) -> ToolOutcome: ...

    async def store_hypotheses(self, hypotheses: list[HypothesisOut]) -> None: ...

    async def store_reflection(self, ranked: list[RankedHypothesis]) -> None: ...

    async def propose(self, proposal: ProposalOut) -> ProposalResult: ...

    async def escalate(self, reason: EscalationReason) -> None: ...

    async def finish(self, meta: RunMeta) -> None: ...

    async def record_llm_call(
        self, kind: str, prompt: str, response: str, usage: Usage, latency_ms: int
    ) -> None:
        """``audit(llm.call)`` with the redacted prompt and response (AI-024)."""
        ...


# --- the real implementation ----------------------------------------------------------


class Proposer(Protocol):
    """Validates a proposal against the catalogue and live targets and asks the runner
    for a dry run (architecture §5.6 steps 4–5; ``agent/proposer.py``)."""

    async def prepare(self, proposal: ProposalOut) -> PreparedProposal | Refusal: ...


class UnavailableProposer:
    """No runner link configured: every proposal escalates ``dry_run_unavailable``."""

    async def prepare(self, proposal: ProposalOut) -> PreparedProposal | Refusal:
        return Refusal("dry_run_unavailable", ("no runner link is configured",))


SOURCES: dict[str, str] = {
    "detector_signal": "detector",
    "log_query": "loki",
    "metric_query": "prometheus",
    "deploy_list": "deploy_ledger",
    "service_health": "docker",
    "runbook_hit": "runbooks",
}
LLM_AUDIT_MAX_CHARS = 20_000


def _utcnow() -> datetime:
    return datetime.now(UTC)


def render_evidence(ref: str, kind: str, payload: dict[str, Any]) -> str:
    return wrap(ref, SOURCES.get(kind, "tool"), json.dumps(payload, separators=(",", ":")))


@dataclass
class DbEffects:
    """``AgentEffects`` on Postgres and ``ocp:events`` for one incident."""

    pool: asyncpg.Pool
    events: EventStream
    tools: ToolBox
    incident_id: UUID
    provider_name: str
    model: str
    proposer: Proposer = field(default_factory=UnavailableProposer)
    proposal_ttl_seconds: int = PROPOSAL_TTL_SECONDS
    clock: Callable[[], datetime] = _utcnow
    _context: IncidentContext | None = None
    _evidence_ids: dict[str, UUID] = field(default_factory=dict)
    _hypothesis_ids: dict[int, UUID] = field(default_factory=dict)

    @property
    def context(self) -> IncidentContext:
        if self._context is None:
            raise RuntimeError("load_context() first")
        return self._context

    async def load_context(self) -> IncidentContext:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "select id, service, title, window_start, window_end from ocp.incidents"
                " where id = $1",
                self.incident_id,
            )
            if row is None:
                raise LookupError(f"incident {self.incident_id} not found")
            rows = await db.evidence_rows(conn, self.incident_id)
        views = []
        for r in rows:
            self._evidence_ids[r["ref"]] = r["id"]
            views.append(
                EvidenceView(
                    r["ref"],
                    r["kind"],
                    r["summary"],
                    render_evidence(r["ref"], r["kind"], r["payload"]),
                )
            )
        self._context = IncidentContext(
            incident_id=row["id"],
            service=row["service"],
            title=row["title"],
            window_start=row["window_start"],
            window_end=row["window_end"],
            evidence=tuple(views),
        )
        return self._context

    async def run_tool(
        self, name: str, arguments: dict[str, Any], purpose: Literal["gather", "disproof"]
    ) -> ToolOutcome:
        context = self.context
        now = self.clock()
        try:
            result = await self.tools.run(
                name, arguments, context.window_start, context.window_end, now
            )
        except ToolRefusedError as exc:
            log.info("tool refused: %s", name, extra={"incident_id": self.incident_id})
            return ToolOutcome(None, redact(str(exc)))
        payload = redact_payload(result.payload)
        params = redact_payload(result.params)
        summary = redact(summarise(result.kind, params, payload))
        suspicious = looks_suspicious(payload)
        async with self.pool.acquire() as conn, conn.transaction():
            await db.lock_incident(conn, self.incident_id)
            ref = await db.next_ref(conn, self.incident_id)
            evidence_id = await db.insert_evidence(
                conn, self.incident_id, ref, result.kind, purpose, name, params, summary,
                payload, suspicious, now,
            )  # fmt: skip
            version = await db.bump(conn, self.incident_id, now)
            await db.audit(
                conn, "agent", "evidence.added", self.incident_id, "evidence", evidence_id,
                {"ref": ref, "tool": name, "purpose": purpose, "suspicious": suspicious},
            )  # fmt: skip
        self._evidence_ids[ref] = evidence_id
        cut, truncated = cut_evidence_payload(result.kind, payload)
        await self._publish(
            "evidence.added",
            version,
            now,
            {
                "id": str(evidence_id),
                "ref": ref,
                "kind": result.kind,
                "purpose": purpose,
                "tool_name": name,
                "summary": summary,
                "payload": cut,
                "payload_truncated": truncated,
                "suspicious_content": suspicious,
                "created_at": now.isoformat().replace("+00:00", "Z"),
            },
        )
        view = EvidenceView(ref, result.kind, summary, render_evidence(ref, result.kind, payload))
        return ToolOutcome(view, view.rendered)

    async def store_hypotheses(self, hypotheses: list[HypothesisOut]) -> None:
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            await db.lock_incident(conn, self.incident_id)
            for h in hypotheses:
                self._hypothesis_ids[h.rank] = await db.insert_hypothesis(
                    conn, self.incident_id, h.rank, h.summary, h.root_cause_category,
                    h.confidence, [self._evidence_ids[r] for r in h.evidence_refs], now,
                )  # fmt: skip
            version = await db.bump(conn, self.incident_id, now)
            await db.audit(
                conn, "agent", "hypothesis.created", self.incident_id, payload={
                    "hypotheses": [
                        {"rank": h.rank, "category": h.root_cause_category,
                         "confidence": h.confidence, "refs": h.evidence_refs}
                        for h in hypotheses
                    ]
                },
            )  # fmt: skip
            current = await self._hypotheses(conn)
        await self._publish("hypothesis.updated", version, now, {"hypotheses": current})

    async def store_reflection(self, ranked: list[RankedHypothesis]) -> None:
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            await db.lock_incident(conn, self.incident_id)
            await db.park_ranks(conn, self.incident_id)
            for r in ranked:
                await db.update_hypothesis(
                    conn, self._hypothesis_ids[r.original_rank], r.hypothesis.rank,
                    r.confidence, r.dropped, r.reflection_notes, now,
                )  # fmt: skip
            version = await db.bump(conn, self.incident_id, now)
            await db.audit(
                conn, "agent", "hypothesis.reflected", self.incident_id, payload={
                    "hypotheses": [
                        {"rank": r.hypothesis.rank, "from_rank": r.original_rank,
                         "confidence_initial": r.confidence_initial,
                         "confidence": r.confidence, "dropped": r.dropped}
                        for r in ranked
                    ]
                },
            )  # fmt: skip
            current = await self._hypotheses(conn)
        await self._publish("hypothesis.updated", version, now, {"hypotheses": current})

    async def propose(self, proposal: ProposalOut) -> ProposalResult:
        prepared = await self.proposer.prepare(proposal)
        if isinstance(prepared, Refusal):
            if prepared.outcome == "invalid":
                await self._audit_rejected(proposal, prepared.problems)
            log.warning(
                "proposal not stored: %s", prepared.outcome, extra={"incident_id": self.incident_id}
            )
            return prepared.outcome
        return await self._store_proposal(proposal, prepared)

    async def _audit_rejected(self, proposal: ProposalOut, problems: tuple[str, ...]) -> None:
        """``audit(proposal.rejected_by_validator)``; the model's values are redacted."""
        async with self.pool.acquire() as conn:
            await db.audit(
                conn, "agent", "proposal.rejected_by_validator", self.incident_id,
                "incident", self.incident_id, {
                    "action": proposal.action,
                    "params": redact_payload(proposal.params),
                    "problems": [redact(p) for p in problems],
                },
            )  # fmt: skip

    async def _store_proposal(self, proposal: ProposalOut, p: PreparedProposal) -> ProposalResult:
        """§5.6 step 6: the pending proposal, ``awaiting_approval``, then the events."""
        async with self.pool.acquire() as conn:
            hypothesis_id = await proposals_db.top_hypothesis_id(conn, self.incident_id)
        text = ProposalText(
            expected_effect=proposal.expected_effect,
            rationale=proposal.rationale,
            evidence_ids=[self._evidence_ids[r] for r in proposal.evidence_refs],
            evidence_refs=list(proposal.evidence_refs),
            hypothesis_id=hypothesis_id,
        )
        stored = await store_proposal(
            self.pool, self.events, self.incident_id, p, text, self.clock(),
            ttl_seconds=self.proposal_ttl_seconds,
        )  # fmt: skip
        # Not stored: the incident left `investigating` meanwhile; escalating is a no-op then.
        return "proposed" if stored else "dry_run_unavailable"

    async def escalate(self, reason: EscalationReason) -> None:
        await escalate_incident(self.pool, self.events, self.incident_id, reason, self.clock())

    async def finish(self, meta: RunMeta) -> None:
        async with self.pool.acquire() as conn:
            await db.merge_agent_meta(
                conn,
                self.incident_id,
                {
                    "tool_calls": meta.tool_calls,
                    "llm_calls": meta.llm_calls,
                    "input_tokens": meta.input_tokens,
                    "output_tokens": meta.output_tokens,
                    "duration_ms": meta.duration_ms,
                    "terminal_state": meta.terminal_state,
                },
            )

    async def record_llm_call(
        self, kind: str, prompt: str, response: str, usage: Usage, latency_ms: int
    ) -> None:
        async with self.pool.acquire() as conn:
            await db.audit(
                conn, "agent", "llm.call", self.incident_id, payload={
                    "kind": kind,
                    "provider": self.provider_name,
                    "model": self.model,
                    "prompt": redact(prompt)[:LLM_AUDIT_MAX_CHARS],
                    "response": redact(response)[:LLM_AUDIT_MAX_CHARS],
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "latency_ms": latency_ms,
                },
            )  # fmt: skip

    async def _hypotheses(self, conn: Any) -> list[dict[str, Any]]:
        ref_of = {v: k for k, v in self._evidence_ids.items()}
        return [
            {
                "id": str(r["id"]),
                "rank": r["rank"],
                "summary": r["summary"],
                "root_cause_category": r["root_cause_category"],
                "confidence": float(r["confidence"]),
                "confidence_initial": float(r["confidence_initial"]),
                "evidence_refs": [ref_of[i] for i in r["evidence_ids"] if i in ref_of],
                "status": r["status"],
                "reflection_notes": r["reflection_notes"],
            }
            for r in await db.all_hypotheses(conn, self.incident_id)
        ]

    async def _publish(self, event: str, version: int, ts: datetime, data: dict[str, Any]) -> None:
        try:
            await self.events.publish(event, self.incident_id, version, ts, data)
        except RedisError as exc:
            # Committed already; the app still sees it through GET /incidents/{id}.
            log.error(
                "publish %s failed: %s", event, type(exc).__name__,
                extra={"incident_id": self.incident_id},
            )  # fmt: skip
