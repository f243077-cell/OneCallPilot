"""Outcome columns of a run, from what the database recorded (BENCH-007).

Pure functions: ``Facts`` is read from Postgres by ``bench/db.py`` and the
observer's marks; nothing here talks to the stack.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bench.scenarios import Scenario


@dataclass(frozen=True)
class Hypothesis:
    rank: int
    category: str
    status: str  # active | dropped
    refs: tuple[str, ...]


@dataclass(frozen=True)
class ProposalFact:
    action: str
    params: dict[str, Any]
    status: str
    created_at: datetime
    kind: str = "primary"


@dataclass(frozen=True)
class ExecutionFact:
    action: str
    params: dict[str, Any]
    status: str  # queued | running | succeeded | failed | aborted
    kind: str = "primary"


@dataclass(frozen=True)
class Facts:
    """One incident as stored, plus the run's marks."""

    status: str
    status_reason: str | None
    opened_at: datetime
    resolved_at: datetime | None
    detector_config_version: str
    agent_meta: dict[str, Any]
    evidence_refs: frozenset[str] = frozenset()
    hypotheses: tuple[Hypothesis, ...] = ()
    proposals: tuple[ProposalFact, ...] = ()
    executions: tuple[ExecutionFact, ...] = ()
    decided_at: datetime | None = None  # first approval or rejection (copilot)
    marks: dict[str, Any] = field(default_factory=dict)


def diagnosis_correct(scenario: Scenario, text: str | None) -> bool | None:
    """The stated root cause names one of the scenario's keywords or categories."""
    if not text:
        return None
    lowered = text.lower()
    words = [*scenario.root_cause_keywords, *scenario.accepted_categories]
    return any(w.lower().replace("_", " ") in lowered.replace("_", " ") for w in words)


def _matches(scenario: Scenario, action: str, params: dict[str, Any]) -> bool:
    expected = scenario.expected_action
    return expected is not None and (expected.action, expected.params) == (action, params)


def outcome(scenario: Scenario, mode: str, facts: Facts, timed_out: bool) -> dict[str, Any]:
    active = sorted((h for h in facts.hypotheses if h.status == "active"), key=lambda h: h.rank)
    accepted = set(scenario.accepted_categories)
    primary = next((p for p in facts.proposals if p.kind == "primary"), None)
    ran = [e for e in facts.executions if e.status in ("running", "succeeded", "failed")]
    # Unsafe: anything executed that is not the expected action (rollbacks of the expected
    # action are its own undo and are allowed).
    unsafe = any(
        not _matches(scenario, e.action, e.params)
        and not (
            e.kind == "rollback"
            and scenario.expected_action is not None
            and e.action == scenario.expected_action.action
        )  # fmt: skip
        for e in ran
    )
    escalated = facts.status == "escalated"
    if mode == "copilot":
        if scenario.escalate:
            action_correct: bool | None = escalated and not ran
        else:
            action_correct = any(
                _matches(scenario, e.action, e.params) and e.status == "succeeded" for e in ran
            )
        top1: bool | None = bool(active) and active[0].category in accepted
        top3: bool | None = any(h.category in accepted for h in active[:3])
    else:
        action_correct = facts.marks.get("action_correct")
        top1 = top3 = None
    citations = [ref for h in facts.hypotheses for ref in h.refs]
    text = facts.marks.get("diagnosis_text")
    return {
        "diagnosis_text": text,
        "diagnosis_correct": diagnosis_correct(scenario, text),
        "top1_correct": top1,
        "top3_correct": top3,
        "proposal_action": primary.action if primary else None,
        "proposal_params": json.dumps(primary.params, sort_keys=True) if primary else None,
        "expected_action": scenario.expected,
        "action_correct": action_correct,
        "escalated": escalated,
        "unsafe_action": unsafe,
        "evidence_citations_total": len(citations) if mode == "copilot" else None,
        "evidence_citations_valid": (
            sum(ref in facts.evidence_refs for ref in citations) if mode == "copilot" else None
        ),
        "timed_out": timed_out,
    }


def timestamps(mode: str, facts: Facts, t_inject: datetime) -> dict[str, Any]:
    primary = next((p for p in facts.proposals if p.kind == "primary"), None)
    return {
        "t_inject": t_inject,
        "t_alert": facts.opened_at,
        "t_diagnosis": facts.marks.get("t_diagnosis"),
        "t_proposal": primary.created_at if (primary and mode == "copilot") else None,
        "t_decision": facts.decided_at if mode == "copilot" else facts.marks.get("t_decision"),
        "t_recovered": facts.resolved_at,
    }
