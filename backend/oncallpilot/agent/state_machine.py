"""The agent's bounded state machine (architecture §6.1, §6.2; AI-001…AI-003, AI-029).

``transition(state, event)`` is a pure function: no I/O, no clock, no LLM.
The orchestrator (``oncallpilot.agent.run``) performs the side effects and
feeds their outcomes back in as events. Budgets live in the state, so every
limit is decided here:

- tool calls: ≤ 6 gather, exactly 1 disproof, ≤ 1 ``propose_action``
  (8 in all); an invalid call still counts;
- gather turns: ≤ 7;
- LLM calls: ≤ 15 (7 gather turns + 4 structured calls + ≤ 1 repair each);
  a second failure of one structured call escalates ``llm_output_invalid``;
- wall clock: a ``Timeout`` event escalates ``budget_exhausted`` from any
  non-terminal phase (the evidence gathered so far stays).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from oncallpilot_contracts.enums import EscalationReason

Phase = Literal[
    "TRIGGERED", "GATHERING", "HYPOTHESIZING", "REFLECTING", "PROPOSING", "PROPOSED", "ESCALATED"
]
StructuredCall = Literal["hypothesize", "disproof_plan", "reflect", "propose"]

MAX_GATHER_CALLS = 6
MAX_GATHER_TURNS = 7
MAX_TOOL_CALLS = 8
MAX_LLM_CALLS = 15
TERMINAL: frozenset[Phase] = frozenset({"PROPOSED", "ESCALATED"})


@dataclass(frozen=True)
class AgentState:
    phase: Phase = "TRIGGERED"
    gather_calls: int = 0
    gather_turns: int = 0
    disproof_calls: int = 0
    propose_calls: int = 0
    llm_calls: int = 0
    repairs: frozenset[StructuredCall] = frozenset()
    reflect_step: Literal["disproof_plan", "disproof_tool", "verdict"] = "disproof_plan"
    escalation_reason: EscalationReason | None = None

    @property
    def tool_calls(self) -> int:
        return self.gather_calls + self.disproof_calls + self.propose_calls

    @property
    def terminal(self) -> bool:
        return self.phase in TERMINAL

    @property
    def gather_calls_left(self) -> int:
        return MAX_GATHER_CALLS - self.gather_calls

    @property
    def can_gather(self) -> bool:
        """Another gather turn is allowed."""
        return (
            self.phase == "GATHERING"
            and self.gather_calls < MAX_GATHER_CALLS
            and self.gather_turns < MAX_GATHER_TURNS
        )


# --- events -----------------------------------------------------------------


@dataclass(frozen=True)
class SeedLoaded:
    """E1 (the detector signal) is loaded; seed evidence is free."""


@dataclass(frozen=True)
class GatherTurn:
    """One gather LLM call returned and ``calls`` of its tool calls were made
    (each counts, valid or not); ``done`` = the model asked for no more."""

    calls: int
    done: bool


@dataclass(frozen=True)
class ToolCalled:
    """The disproof query or ``propose_action`` was made (valid or not, it counts)."""

    purpose: Literal["disproof", "propose"]


@dataclass(frozen=True)
class StructuredAttempt:
    """One structured LLM call returned; ``valid`` = it validated."""

    call: StructuredCall
    valid: bool


@dataclass(frozen=True)
class HypothesesChecked:
    """Hypotheses validated against the evidence refs; ``valid`` survived."""

    valid: int


@dataclass(frozen=True)
class ReflectionDone:
    """Code rules applied; ``top_confidence`` is None when every hypothesis was dropped."""

    top_confidence: float | None
    threshold: float


@dataclass(frozen=True)
class ProposalChecked:
    """The outcome of the proposal step (§5.6)."""

    outcome: Literal["proposed", "none", "invalid", "dry_run_unavailable"]


@dataclass(frozen=True)
class LLMUnavailable:
    """The provider failed after its own retry."""


@dataclass(frozen=True)
class Timeout:
    """MAX_AGENT_SECONDS since job pickup."""


Event = (
    SeedLoaded
    | GatherTurn
    | ToolCalled
    | StructuredAttempt
    | HypothesesChecked
    | ReflectionDone
    | ProposalChecked
    | LLMUnavailable
    | Timeout
)


class IllegalTransitionError(Exception):
    """An event that cannot happen in the current phase (a bug in the orchestrator)."""


def escalate(state: AgentState, reason: EscalationReason) -> AgentState:
    return replace(state, phase="ESCALATED", escalation_reason=reason)


def _after_gather(state: AgentState) -> AgentState:
    return state if state.can_gather else replace(state, phase="HYPOTHESIZING")


def transition(state: AgentState, event: Event) -> AgentState:  # noqa: C901 - one table
    if state.terminal:
        raise IllegalTransitionError(f"{type(event).__name__} after {state.phase}")

    if isinstance(event, Timeout):
        return escalate(state, "budget_exhausted")
    if isinstance(event, LLMUnavailable):
        return escalate(state, "llm_unavailable")

    if isinstance(event, SeedLoaded):
        if state.phase != "TRIGGERED":
            raise IllegalTransitionError("seed evidence loaded twice")
        return replace(state, phase="GATHERING")

    if isinstance(event, GatherTurn):
        if not state.can_gather:
            raise IllegalTransitionError("gather turn not allowed")
        if event.calls < 0 or state.gather_calls + event.calls > MAX_GATHER_CALLS:
            raise IllegalTransitionError("gather tool calls over budget")
        state = replace(
            state,
            gather_turns=state.gather_turns + 1,
            gather_calls=state.gather_calls + event.calls,
            llm_calls=state.llm_calls + 1,
        )
        return replace(state, phase="HYPOTHESIZING") if event.done else _after_gather(state)

    if isinstance(event, ToolCalled):
        if event.purpose == "disproof":
            if state.phase != "REFLECTING" or state.reflect_step != "disproof_tool":
                raise IllegalTransitionError("disproof call out of order")
            return replace(state, disproof_calls=1, reflect_step="verdict")
        if state.phase != "PROPOSING" or state.propose_calls >= 1:
            raise IllegalTransitionError("propose_action called twice")
        return replace(state, propose_calls=1)

    if isinstance(event, StructuredAttempt):
        expected = _structured_call(state)
        if event.call != expected:
            raise IllegalTransitionError(f"{event.call} in {state.phase}")
        state = replace(state, llm_calls=state.llm_calls + 1)
        if event.valid:
            if event.call == "disproof_plan":
                return replace(state, reflect_step="disproof_tool")
            return state
        if event.call in state.repairs:
            return escalate(state, "llm_output_invalid")
        return replace(state, repairs=state.repairs | {event.call})

    if isinstance(event, HypothesesChecked):
        if state.phase != "HYPOTHESIZING":
            raise IllegalTransitionError("hypotheses outside HYPOTHESIZING")
        if event.valid == 0:
            return escalate(state, "low_confidence")
        return replace(state, phase="REFLECTING", reflect_step="disproof_plan")

    if isinstance(event, ReflectionDone):
        if state.phase != "REFLECTING" or state.reflect_step != "verdict":
            raise IllegalTransitionError("reflection before the disproof query")
        if event.top_confidence is None or event.top_confidence < event.threshold:
            return escalate(state, "low_confidence")
        return replace(state, phase="PROPOSING")

    if isinstance(event, ProposalChecked):
        if state.phase != "PROPOSING":
            raise IllegalTransitionError("proposal outside PROPOSING")
        reasons: dict[str, EscalationReason] = {
            "none": "no_catalogue_action_fits",
            "invalid": "proposal_invalid",
            "dry_run_unavailable": "dry_run_unavailable",
        }
        if event.outcome == "proposed":
            return replace(state, phase="PROPOSED")
        return escalate(state, reasons[event.outcome])

    raise IllegalTransitionError(f"unknown event {event!r}")  # pragma: no cover


def _structured_call(state: AgentState) -> StructuredCall | None:
    if state.phase == "HYPOTHESIZING":
        return "hypothesize"
    if state.phase == "PROPOSING":
        return "propose"
    if state.phase == "REFLECTING":
        if state.reflect_step == "disproof_plan":
            return "disproof_plan"
        if state.reflect_step == "verdict":
            return "reflect"
    return None
