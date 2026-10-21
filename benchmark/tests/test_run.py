"""bench run with fakes: cleanup, guards, timing, outcome columns (BENCH-004…BENCH-007)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from bench import results
from bench.api import Api
from bench.judge import ExecutionFact, Facts, Hypothesis, ProposalFact, diagnosis_correct, outcome
from bench.marks import Marks
from bench.plan import PlannedRun
from bench.run import BenchAbort, Deps, run_one
from bench.scenarios import load
from bench.stack import Stack, StackError, redis_password

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
SCENARIOS = load()
BAD_DEPLOY, SLOW = SCENARIOS[1], SCENARIOS[6]
ROLLBACK = {"service": "api", "target_release": "1.4.0"}


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeStack:
    def __init__(self, agent: bool = True) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.agent = agent

    def chaos(self, *args: str) -> None:
        self.calls.append(("chaos", *args))

    def clear_limits(self) -> None:
        self.calls.append(("clear_limits",))

    def set_current_run(self, run_id: str) -> None:
        self.calls.append(("set_run", run_id))

    def clear_current_run(self) -> None:
        self.calls.append(("clear_run",))

    def agent_enabled(self) -> bool:
        return self.agent

    def git_sha(self) -> str:
        return "abc123"


class FakeApi:
    def __init__(self, leftovers: list[str] | None = None, busy: bool = False) -> None:
        self.open = list(leftovers or [])
        self.busy = busy
        self.resolved: list[tuple[str, str]] = []

    def open_incidents(self) -> list[dict[str, Any]]:
        return [{"id": i, "status": "escalated"} for i in self.open]

    def resolve(self, incident_id: str, reason: str) -> str:
        if self.busy:
            return "busy"
        self.resolved.append((incident_id, reason))
        self.open.remove(incident_id)
        return "resolved"


def facts(**overrides: Any) -> Facts:
    base = Facts(
        status="awaiting_approval", status_reason=None, opened_at=T0, resolved_at=None,
        detector_config_version="cfg1",
        agent_meta={"provider": "gemini", "model": "m-1", "prompt_version": "v1",
                    "catalogue_version": "1.0.0"},
        evidence_refs=frozenset({"E1", "E2", "E3"}),
        hypotheses=(Hypothesis(1, "bad_deploy", "active", ("E1", "E3")),
                    Hypothesis(2, "application_bug", "active", ("E2", "E9"))),
    )  # fmt: skip
    return replace(base, **overrides)


class FakeDb:
    """Incident appears 2 polls after inject; its state follows a script of Facts."""

    def __init__(self, clock: Clock, script: list[Facts], opened_offset_s: int = 30) -> None:
        self.clock = clock
        self.script = script
        self.opened_offset = timedelta(seconds=opened_offset_s)
        self.t_inject: datetime | None = None
        self.marks_seen: dict[str, Any] = {}

    def run_incident(self, run_id: str) -> tuple[str, datetime] | None:
        if self.t_inject is None:
            self.t_inject = self.clock.now
            return None
        return "inc-1", self.t_inject + self.opened_offset

    def facts(self, incident_id: str, marks: dict[str, Any]) -> Facts:
        current = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return replace(current, marks=marks)


def planned(scenario_id: int = 2, mode: str = "copilot") -> PlannedRun:
    name = SCENARIOS[scenario_id - 1].name
    return PlannedRun("R7-01", 7, scenario_id, name, mode, 1, "tanzeel", "observer2")  # type: ignore[arg-type]


def deps(tmp_path: Path, clock: Clock, db: FakeDb, **kw: Any) -> Deps:
    return Deps(
        stack=kw.get("stack", FakeStack()), api=kw.get("api", FakeApi()), db=db,
        marks=Marks(tmp_path, clock), clock=clock, sleep=clock.sleep,
    )  # fmt: skip


def test_copilot_run_end_to_end(tmp_path: Path) -> None:
    clock = Clock()
    done = facts(
        status="resolved", resolved_at=T0 + timedelta(minutes=9), decided_at=T0,
        proposals=(ProposalFact("rollback_deploy", ROLLBACK, "succeeded",
                                T0 + timedelta(minutes=5)),),
        executions=(ExecutionFact("rollback_deploy", ROLLBACK, "succeeded"),),
    )  # fmt: skip
    db = FakeDb(clock, [facts(), facts(), done])
    api = FakeApi(leftovers=["old-1"])
    d = deps(tmp_path, clock, db, api=api)
    row = run_one(planned(), BAD_DEPLOY, d)

    assert api.resolved == [("old-1", "bench_cleanup")]
    stack = d.stack
    assert isinstance(stack, FakeStack)
    assert stack.calls == [("chaos", "reset"), ("clear_limits",), ("set_run", "R7-01"),
                           ("chaos", "inject", "bad-deploy"), ("clear_run",)]  # fmt: skip
    assert row["t_inject"] == T0 + timedelta(seconds=240)  # warm-up + baseline
    assert row["t_alert"] == T0 and row["t_recovered"] == T0 + timedelta(minutes=9)
    assert (row["llm_model"], row["detector_config_version"], row["git_sha"]) == (
        "m-1", "cfg1", "abc123",
    )  # fmt: skip
    assert row["action_correct"] is True and row["unsafe_action"] is False
    assert (row["top1_correct"], row["top3_correct"]) == (True, True)
    assert (row["evidence_citations_total"], row["evidence_citations_valid"]) == (4, 3)
    assert row["proposal_params"] == json.dumps(ROLLBACK, sort_keys=True)
    assert row["timed_out"] is False
    results.append_row(row, tmp_path / "runs.csv")  # a valid CSV row


def test_marks_reach_the_row(tmp_path: Path) -> None:
    clock = Clock()
    marks = Marks(tmp_path, clock)

    class MarkingDb(FakeDb):
        def facts(self, incident_id: str, m: dict[str, Any]) -> Facts:
            if "t_diagnosis" not in m:
                marks.mark("diagnosis", "the 1.5.0 deploy broke checkout")
                marks.mark("note", "operator opened Grafana after escalation")
            return super().facts(incident_id, marks.read("R7-01"))

    db = MarkingDb(clock, [facts(), facts(status="resolved", resolved_at=T0)])
    d = deps(tmp_path, clock, db)
    d.marks = marks
    row = run_one(planned(), BAD_DEPLOY, d)
    assert row["diagnosis_text"] == "the 1.5.0 deploy broke checkout"
    assert row["diagnosis_correct"] is True and row["t_diagnosis"] is not None
    assert row["notes"] == "operator opened Grafana after escalation"
    assert marks.current() is None  # the run is closed


def test_escalation_is_the_end_for_escalate_scenarios(tmp_path: Path) -> None:
    clock = Clock()
    slow = Hypothesis(1, "dependency_slow", "active", ("E2",))
    escalated = facts(
        status="escalated", status_reason="no_catalogue_action_fits", hypotheses=(slow,)
    )
    db = FakeDb(clock, [escalated])
    row = run_one(planned(7), SLOW, deps(tmp_path, clock, db))
    assert (row["escalated"], row["action_correct"], row["timed_out"]) == (True, True, False)
    assert row["expected_action"] == "escalate"


def test_timeout(tmp_path: Path) -> None:
    clock = Clock()
    row = run_one(planned(), BAD_DEPLOY, deps(tmp_path, clock, FakeDb(clock, [facts()])))
    assert row["timed_out"] is True and row["t_recovered"] is None


def test_stale_incident_marks_the_run_invalid(tmp_path: Path) -> None:
    clock = Clock()
    db = FakeDb(clock, [facts()], opened_offset_s=-60)  # opened before t_inject
    row = run_one(planned(), BAD_DEPLOY, deps(tmp_path, clock, db))
    assert row["notes"] == "invalid_run"
    results.append_row(row, tmp_path / "runs.csv")


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"api": FakeApi(leftovers=["old-1"], busy=True)}, "busy"),
        ({"stack": FakeStack(agent=False)}, "AGENT_ENABLED"),
    ],
)
def test_aborts_write_nothing(tmp_path: Path, kw: dict[str, Any], message: str) -> None:
    clock = Clock()
    with pytest.raises(BenchAbort, match=message):
        run_one(planned(), BAD_DEPLOY, deps(tmp_path, clock, FakeDb(clock, [facts()]), **kw))


def test_cleanup_gives_up_after_30_s(tmp_path: Path) -> None:
    class Sticky(FakeApi):
        def resolve(self, incident_id: str, reason: str) -> str:
            return "resolved"  # says so, but the incident stays open

    clock = Clock()
    with pytest.raises(BenchAbort, match="still open"):
        run_one(planned(), BAD_DEPLOY,
                deps(tmp_path, clock, FakeDb(clock, [facts()]), api=Sticky(["x"])))  # fmt: skip


def test_unsafe_action_and_manual_mode_outcomes() -> None:
    wrong = facts(executions=(ExecutionFact("clear_cache", {"cache": "catalog"}, "succeeded"),))
    out = outcome(BAD_DEPLOY, "copilot", wrong, timed_out=False)
    assert out["unsafe_action"] is True and out["action_correct"] is False
    # A rollback of the expected action is its own undo, not unsafe.
    undo = facts(executions=(
        ExecutionFact("rollback_deploy", ROLLBACK, "failed"),
        ExecutionFact("rollback_deploy", {"service": "api", "target_release": "1.5.0"},
                      "succeeded", kind="rollback"),
    ))  # fmt: skip
    assert outcome(BAD_DEPLOY, "copilot", undo, timed_out=False)["unsafe_action"] is False
    manual = outcome(BAD_DEPLOY, "manual", facts(marks={"action_correct": True}), False)
    assert manual["action_correct"] is True and manual["top1_correct"] is None
    assert manual["evidence_citations_total"] is None
    assert diagnosis_correct(BAD_DEPLOY, "the cache is down") is False
    assert diagnosis_correct(BAD_DEPLOY, None) is None


def test_mark_needs_a_run(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no run"):
        Marks(tmp_path).mark("decision")


# --- stack and API boundaries ---------------------------------------------------------


class Recorder:
    def __init__(self, outputs: dict[str, str] | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []
        self.outputs = outputs or {}
        self.fail = fail

    def run(
        self, argv: Sequence[str], env: dict[str, str] | None = None, timeout: float = 300
    ) -> tuple[int, str]:
        self.calls.append((list(argv), env))
        key = next((k for k in self.outputs if k in argv), None)
        return (1 if self.fail else 0), (self.outputs[key] if key else "")


def test_stack_commands_never_put_the_password_on_the_command_line() -> None:
    rec = Recorder({"ocp:ratelimit:*": "ocp:ratelimit:api\n", "printenv": "true\n"})
    stack = Stack(rec, redis_password("redis://backend:s3cret-pw@copilot-redis:6379/0"))
    stack.clear_limits()
    stack.set_current_run("R7-01")
    assert stack.agent_enabled() is True
    deleted = [argv for argv, _ in rec.calls if "DEL" in argv]
    assert deleted and deleted[0][-1] == "ocp:ratelimit:api"
    for argv, env in rec.calls:
        assert "s3cret-pw" not in " ".join(argv)
        if "redis-cli" in argv:
            assert env == {"REDISCLI_AUTH": "s3cret-pw"} and "--user" in argv
    with pytest.raises(StackError):
        Stack(Recorder(fail=True), "x").chaos("reset")
    with pytest.raises(StackError):
        redis_password("redis://copilot-redis:6379/0")


def test_api_signs_in_and_resolves() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/auth/v1/token":
            return httpx.Response(200, json={"access_token": "at-7f3a91"})
        if request.url.path == "/incidents":
            if "cursor" not in request.url.params:
                return httpx.Response(200, json={"items": [{"id": "a", "status": "escalated"},
                                                           {"id": "b", "status": "resolved"}],
                                                 "next_cursor": "c2"})  # fmt: skip
            return httpx.Response(200, json={"items": [{"id": "c", "status": "investigating"}],
                                             "next_cursor": None})  # fmt: skip
        return httpx.Response(409 if request.url.path.startswith("/incidents/c") else 200)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        api = Api(http, "http://api", "http://supa", "anon", "t@example.test", "pw")
        assert [i["id"] for i in api.open_incidents()] == ["a", "c"]
        assert api.resolve("a", "bench_cleanup") == "resolved"
        assert api.resolve("c", "bench_cleanup") == "busy"
    token_call = seen[0]
    assert token_call.url.params["grant_type"] == "password"
    assert token_call.headers["apikey"] == "anon"
    assert all(r.headers.get("authorization") == "Bearer at-7f3a91" for r in seen[1:])
    assert all("at-7f3a91" not in str(r.url) for r in seen)
