"""Fakes for agent tests: effects that record calls, a stub prompt book, and helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from oncallpilot_contracts.enums import EscalationReason

from oncallpilot.agent.effects import (
    EvidenceView,
    IncidentContext,
    ProposalResult,
    RankedHypothesis,
    RunMeta,
    ToolOutcome,
)
from oncallpilot.agent.providers.base import LLMTurn, ToolCall, Usage
from oncallpilot.agent.schemas import HypothesisOut, ProposalOut
from oncallpilot.agent.tools.registry import READ_TOOLS
from oncallpilot.agent.untrusted import wrap

T0 = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)


class StubPrompts:
    def system(self) -> str:
        return "system"

    def gather(self) -> str:
        return "gather"

    def hypothesize(self) -> str:
        return "hypothesize"

    def disproof(self) -> str:
        return "disproof"

    def reflect(self) -> str:
        return "reflect"

    def propose(self) -> str:
        return "propose"


@dataclass
class FakeEffects:
    proposal_result: ProposalResult = "proposed"
    tool_calls: list[tuple[str, dict[str, Any], str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=lambda: ["E1"])
    hypotheses: list[HypothesisOut] = field(default_factory=list)
    reflection: list[RankedHypothesis] = field(default_factory=list)
    proposals: list[ProposalOut] = field(default_factory=list)
    escalated: list[EscalationReason] = field(default_factory=list)
    meta: list[RunMeta] = field(default_factory=list)

    async def load_context(self) -> IncidentContext:
        seed = EvidenceView(
            "E1", "detector_signal", "api error rate 31%", wrap("E1", "detector", "{}")
        )
        return IncidentContext(
            incident_id=uuid.uuid4(),
            service="api",
            title="api error rate 31%",
            window_start=T0,
            window_end=None,
            evidence=(seed,),
        )

    async def run_tool(
        self, name: str, arguments: dict[str, Any], purpose: Literal["gather", "disproof"]
    ) -> ToolOutcome:
        self.tool_calls.append((name, arguments, purpose))
        if name not in READ_TOOLS:
            return ToolOutcome(None, f"unknown tool {name}")
        if arguments.get("invalid"):
            return ToolOutcome(None, "invalid parameters")
        ref = f"E{len(self.evidence) + 1}"
        self.evidence.append(ref)
        view = EvidenceView(ref, "log_query", f"{name} result", wrap(ref, "loki", "lines"))
        return ToolOutcome(view, view.rendered)

    async def store_hypotheses(self, hypotheses: list[HypothesisOut]) -> None:
        self.hypotheses = hypotheses

    async def store_reflection(self, ranked: list[RankedHypothesis]) -> None:
        self.reflection = ranked

    async def propose(self, proposal: ProposalOut) -> ProposalResult:
        self.proposals.append(proposal)
        return self.proposal_result

    async def escalate(self, reason: EscalationReason) -> None:
        self.escalated.append(reason)

    async def finish(self, meta: RunMeta) -> None:
        self.meta.append(meta)


def tool_turn(*names: str, done: bool = False, **arguments: Any) -> LLMTurn:
    calls = tuple(
        ToolCall(id=f"c{i}", name=name, arguments=dict(arguments)) for i, name in enumerate(names)
    )
    return LLMTurn(tool_calls=calls, done=done, usage=Usage(50, 10))


def hypotheses(*specs: tuple[float, list[str]]) -> dict[str, Any]:
    return {
        "hypotheses": [
            {
                "rank": i,
                "summary": f"hypothesis number {i} about the deploy",
                "root_cause_category": "bad_deploy",
                "confidence": confidence,
                "evidence_refs": refs,
            }
            for i, (confidence, refs) in enumerate(specs, start=1)
        ]
    }


DISPROOF = {
    "target_rank": 1,
    "tool": "get_recent_deploys",
    "params": {"service": "api"},
    "expectation_if_true": "a deploy shortly before the errors",
}


def reflection(*adjusted: float, verdict: str = "survived") -> dict[str, Any]:
    return {
        "items": [
            {"rank": i, "citations_supported": True, "adjusted_confidence": a, "note": "ok"}
            for i, a in enumerate(adjusted, start=1)
        ],
        "top_verdict": verdict,
    }


def proposal(action: str = "rollback_deploy", refs: list[str] | None = None) -> dict[str, Any]:
    return {
        "action": action,
        "params": {"service": "api", "to_release": "1.4.0"} if action != "none" else {},
        "expected_effect": "errors back to baseline",
        "rationale": "the errors began with the 1.5.0 deploy",
        "evidence_refs": refs or ["E1"],
    }


def happy_script() -> dict[str, list[Any]]:
    return {
        "HypothesisSetOut": [hypotheses((0.85, ["E1"]))],
        "DisproofPlanOut": [DISPROOF],
        "ReflectionOut": [reflection(0.82)],
        "ProposalOut": [proposal()],
    }
