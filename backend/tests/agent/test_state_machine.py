"""Every transition of the agent state machine (AI-001: no network, LLM or database)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from oncallpilot.agent.state_machine import (
    MAX_GATHER_CALLS,
    MAX_GATHER_TURNS,
    AgentState,
    GatherTurn,
    HypothesesChecked,
    IllegalTransitionError,
    LLMUnavailable,
    ProposalChecked,
    ReflectionDone,
    SeedLoaded,
    StructuredAttempt,
    Timeout,
    ToolCalled,
    transition,
)

GATHERING = transition(AgentState(), SeedLoaded())
HYPOTHESIZING = replace(GATHERING, phase="HYPOTHESIZING")
REFLECTING = replace(GATHERING, phase="REFLECTING", reflect_step="disproof_plan")
VERDICT = replace(REFLECTING, reflect_step="verdict", disproof_calls=1)
PROPOSING = replace(GATHERING, phase="PROPOSING")


def test_triggered_to_gathering() -> None:
    assert GATHERING.phase == "GATHERING"
    with pytest.raises(IllegalTransitionError):
        transition(GATHERING, SeedLoaded())


def test_gather_turns_count_calls_and_llm_calls() -> None:
    state = transition(GATHERING, GatherTurn(calls=2, done=False))
    assert (state.phase, state.gather_calls, state.gather_turns, state.llm_calls) == (
        "GATHERING",
        2,
        1,
        1,
    )
    assert transition(state, GatherTurn(calls=0, done=True)).phase == "HYPOTHESIZING"


def test_six_gather_calls_end_gathering() -> None:
    state = transition(GATHERING, GatherTurn(calls=MAX_GATHER_CALLS, done=False))
    assert state.phase == "HYPOTHESIZING"
    with pytest.raises(IllegalTransitionError):
        transition(GATHERING, GatherTurn(calls=MAX_GATHER_CALLS + 1, done=False))
    with pytest.raises(IllegalTransitionError):
        transition(GATHERING, GatherTurn(calls=-1, done=False))


def test_seven_gather_turns_end_gathering() -> None:
    state = GATHERING
    for _ in range(MAX_GATHER_TURNS):
        state = transition(state, GatherTurn(calls=0, done=False))
    assert state.phase == "HYPOTHESIZING" and state.gather_turns == 7
    with pytest.raises(IllegalTransitionError):
        transition(state, GatherTurn(calls=0, done=False))


def test_hypotheses_valid_or_none() -> None:
    assert transition(HYPOTHESIZING, HypothesesChecked(valid=2)).phase == "REFLECTING"
    escalated = transition(HYPOTHESIZING, HypothesesChecked(valid=0))
    assert (escalated.phase, escalated.escalation_reason) == ("ESCALATED", "low_confidence")
    with pytest.raises(IllegalTransitionError):
        transition(GATHERING, HypothesesChecked(valid=1))


def test_structured_call_with_one_repair() -> None:
    once = transition(HYPOTHESIZING, StructuredAttempt("hypothesize", valid=False))
    assert once.phase == "HYPOTHESIZING" and once.repairs == {"hypothesize"}
    ok = transition(once, StructuredAttempt("hypothesize", valid=True))
    assert ok.llm_calls == 2 and ok.phase == "HYPOTHESIZING"
    twice = transition(once, StructuredAttempt("hypothesize", valid=False))
    assert (twice.phase, twice.escalation_reason) == ("ESCALATED", "llm_output_invalid")


def test_structured_call_must_match_the_phase() -> None:
    with pytest.raises(IllegalTransitionError):
        transition(HYPOTHESIZING, StructuredAttempt("propose", valid=True))
    with pytest.raises(IllegalTransitionError):
        transition(GATHERING, StructuredAttempt("hypothesize", valid=True))
    with pytest.raises(IllegalTransitionError):
        transition(
            replace(REFLECTING, reflect_step="disproof_tool"), StructuredAttempt("reflect", True)
        )


def test_reflection_sequence() -> None:
    planned = transition(REFLECTING, StructuredAttempt("disproof_plan", valid=True))
    assert planned.reflect_step == "disproof_tool"
    queried = transition(planned, ToolCalled("disproof"))
    assert queried.reflect_step == "verdict" and queried.tool_calls == 1
    judged = transition(queried, StructuredAttempt("reflect", valid=True))
    assert transition(judged, ReflectionDone(0.8, 0.7)).phase == "PROPOSING"
    low = transition(judged, ReflectionDone(0.69, 0.7))
    assert (low.phase, low.escalation_reason) == ("ESCALATED", "low_confidence")
    dropped = transition(judged, ReflectionDone(None, 0.7))
    assert dropped.escalation_reason == "low_confidence"
    assert transition(judged, ReflectionDone(0.7, 0.7)).phase == "PROPOSING"  # boundary


def test_reflection_out_of_order() -> None:
    with pytest.raises(IllegalTransitionError):
        transition(REFLECTING, ToolCalled("disproof"))
    with pytest.raises(IllegalTransitionError):
        transition(REFLECTING, ReflectionDone(0.9, 0.7))
    with pytest.raises(IllegalTransitionError):
        transition(GATHERING, ToolCalled("disproof"))


@pytest.mark.parametrize(
    ("outcome", "phase", "reason"),
    [
        ("proposed", "PROPOSED", None),
        ("none", "ESCALATED", "no_catalogue_action_fits"),
        ("invalid", "ESCALATED", "proposal_invalid"),
        ("dry_run_unavailable", "ESCALATED", "dry_run_unavailable"),
    ],
)
def test_proposal_outcomes(outcome: str, phase: str, reason: str | None) -> None:
    called = transition(PROPOSING, ToolCalled("propose"))
    state = transition(called, ProposalChecked(outcome))  # type: ignore[arg-type]
    assert (state.phase, state.escalation_reason) == (phase, reason)


def test_propose_action_only_once_and_only_when_proposing() -> None:
    called = transition(PROPOSING, ToolCalled("propose"))
    with pytest.raises(IllegalTransitionError):
        transition(called, ToolCalled("propose"))
    with pytest.raises(IllegalTransitionError):
        transition(GATHERING, ToolCalled("propose"))
    with pytest.raises(IllegalTransitionError):
        transition(GATHERING, ProposalChecked("proposed"))


@pytest.mark.parametrize("state", [GATHERING, HYPOTHESIZING, REFLECTING, VERDICT, PROPOSING])
def test_timeout_and_llm_failure_escalate_from_any_open_phase(state: AgentState) -> None:
    assert transition(state, Timeout()).escalation_reason == "budget_exhausted"
    assert transition(state, LLMUnavailable()).escalation_reason == "llm_unavailable"
    assert transition(AgentState(), Timeout()).phase == "ESCALATED"


@pytest.mark.parametrize("terminal", ["PROPOSED", "ESCALATED"])
def test_nothing_after_a_terminal_phase(terminal: str) -> None:
    state = replace(GATHERING, phase=terminal)  # type: ignore[arg-type]
    assert state.terminal
    with pytest.raises(IllegalTransitionError):
        transition(state, Timeout())


def test_tool_call_total_is_bounded_by_construction() -> None:
    state = transition(GATHERING, GatherTurn(calls=6, done=False))
    state = replace(state, phase="REFLECTING", reflect_step="disproof_tool")
    state = transition(state, ToolCalled("disproof"))
    state = replace(state, phase="PROPOSING")
    state = transition(state, ToolCalled("propose"))
    assert state.tool_calls == 8
