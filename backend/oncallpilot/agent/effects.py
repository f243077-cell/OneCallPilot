"""``AgentEffects``: every side effect of an investigation (architecture §6.1; AI-001).

The state machine and the orchestrator never touch the database, Redis, the
tools or the runner directly; they call this interface. Tests replace it with
a fake; the real implementation stores evidence (redacted, with the next
``E#`` ref), hypotheses, proposals and audit rows, and publishes events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from oncallpilot_contracts.enums import EscalationReason, EvidenceKind

from oncallpilot.agent.schemas import HypothesisOut, ProposalOut


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
