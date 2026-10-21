"""``bench run <run_id>``: one benchmark run, end to end (BENCH-004…BENCH-007).

1. ``chaos reset``; resolve every incident that is not resolved
   (``bench_cleanup``); abort if one is busy or any is still open after 30 s.
2. Clear the copilot's and the runner's rate limits and cooldowns.
3. Set ``ocp:bench:current_run`` so the run's incident is stamped with it.
   Check that the agent is enabled exactly in copilot mode (BENCH-005).
4. Wait for the detector warm-up (180 s) plus a 60 s baseline.
5. ``chaos inject <scenario>``, recording ``t_inject``.
6. Wait for this run's incident (stamped, opened at or after ``t_inject``);
   an older stamped incident makes the run ``invalid_run``.
7. Wait until the incident is resolved, or escalated when escalation is the
   expected outcome, or the scenario's timeout passes; then write one row.

An abort (``BenchAbort``) writes no row. Observer marks (``bench mark``) are
read from ``results/marks-<run_id>.json``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from bench.db import Db
from bench.judge import Facts, outcome, timestamps
from bench.marks import Marks
from bench.plan import PlannedRun
from bench.scenarios import Scenario

WARMUP_SECONDS = 180 + 60
CLEANUP_SECONDS = 30
POLL_SECONDS = 5


class BenchAbort(RuntimeError):
    """The run cannot be trusted; nothing is written (BENCH-004)."""


class StackLike(Protocol):
    def chaos(self, *args: str) -> None: ...
    def clear_limits(self) -> None: ...
    def set_current_run(self, run_id: str) -> None: ...
    def clear_current_run(self) -> None: ...
    def agent_enabled(self) -> bool: ...
    def git_sha(self) -> str: ...


class ApiLike(Protocol):
    def open_incidents(self) -> list[dict[str, Any]]: ...
    def resolve(self, incident_id: str, reason: str) -> str: ...


@dataclass
class Deps:
    stack: StackLike
    api: ApiLike
    db: Db
    marks: Marks
    clock: Callable[[], datetime]
    sleep: Callable[[float], None]
    warmup_seconds: float = WARMUP_SECONDS
    poll_seconds: float = POLL_SECONDS


def cleanup(deps: Deps) -> None:
    """BENCH-004 step 1: no leftover incident may become this run's ``t_alert``."""
    for incident in deps.api.open_incidents():
        if deps.api.resolve(str(incident["id"]), "bench_cleanup") == "busy":
            raise BenchAbort(f"incident {incident['id']} is busy (an execution is running)")
    deadline = deps.clock() + timedelta(seconds=CLEANUP_SECONDS)
    while deps.api.open_incidents():
        if deps.clock() >= deadline:
            raise BenchAbort("incidents are still open 30 s after the cleanup")
        deps.sleep(1)


def _wait_for_incident(
    deps: Deps, run: PlannedRun, t_inject: datetime, deadline: datetime
) -> tuple[str | None, bool]:
    """(incident id, valid). ``(None, True)`` means no alert before the deadline."""
    while deps.clock() < deadline:
        found = deps.db.run_incident(run.run_id)
        if found is not None:
            incident_id, opened_at = found
            return incident_id, opened_at >= t_inject
        deps.sleep(deps.poll_seconds)
    return None, True


def _finished(scenario: Scenario, facts: Facts) -> bool:
    return facts.status == "resolved" or (scenario.escalate and facts.status == "escalated")


def run_one(run: PlannedRun, scenario: Scenario, deps: Deps) -> dict[str, Any]:
    if run.scenario_id != scenario.id:
        raise BenchAbort("the plan entry and the scenario do not match")
    deps.stack.chaos("reset")
    cleanup(deps)
    deps.stack.clear_limits()
    if deps.stack.agent_enabled() != (run.mode == "copilot"):
        raise BenchAbort(f"AGENT_ENABLED does not match {run.mode} mode (BENCH-005)")
    deps.stack.set_current_run(run.run_id)
    deps.marks.start(run.run_id)
    try:
        deps.sleep(deps.warmup_seconds)
        deps.stack.chaos("inject", scenario.name)
        t_inject = deps.clock()
        deadline = t_inject + timedelta(minutes=scenario.timeout_minutes)
        incident_id, valid = _wait_for_incident(deps, run, t_inject, deadline)
        row: dict[str, Any] = {
            "run_id": run.run_id, "plan_seed": run.plan_seed, "scenario_id": run.scenario_id,
            "scenario_name": run.scenario_name, "mode": run.mode, "rep": run.rep,
            "operator": run.operator, "observer": run.observer, "git_sha": deps.stack.git_sha(),
            "expected_action": scenario.expected, "t_inject": t_inject,
        }  # fmt: skip
        if incident_id is None or not valid:
            marks = deps.marks.read(run.run_id)
            notes = "no_alert" if incident_id is None else "invalid_run"
            return row | {
                "timed_out": incident_id is None,
                "notes": "; ".join(filter(None, [notes, marks.get("notes")])),
            }
        while True:
            facts = deps.db.facts(incident_id, deps.marks.read(run.run_id))
            timed_out = deps.clock() >= deadline
            if _finished(scenario, facts) or timed_out:
                break
            deps.sleep(deps.poll_seconds)
        meta = facts.agent_meta
        return (
            row
            | {
                "llm_provider": meta.get("provider"),
                "llm_model": meta.get("model"),
                "prompt_version": meta.get("prompt_version"),
                "catalogue_version": meta.get("catalogue_version"),
                "detector_config_version": facts.detector_config_version,
            }
            | timestamps(run.mode, facts, t_inject)
            | outcome(scenario, run.mode, facts, timed_out and not _finished(scenario, facts))
            | {"notes": facts.marks.get("notes")}
        )
    finally:
        deps.stack.clear_current_run()
        deps.marks.finish()
