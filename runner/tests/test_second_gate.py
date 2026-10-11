"""The runner's second gate (A2.5): idempotency (RUN-005), rate limit and cooldown
(RUN-006), drift abort (RUN-008), and the structural check that ends every action
(RUN-014).

The counters live in the in-memory FakeStreams, the same store the dry run reads.
Its key TTLs follow `streams.now_ms`; the rate-limit timestamps follow the test
clock (`clock[0]`), so `advance()` moves both.

Fixtures come from test_execute.py, so pytest sees them here by import.
"""
# ruff: noqa: F811  (each test redefines an imported fixture name as its argument)

import json
import logging
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from oncallpilot_runner.checks import Checker
from oncallpilot_runner.consumer import RECLAIM_IDLE_MS, Consumer
from oncallpilot_runner.dry_run import DryRunner
from oncallpilot_runner.execute import Executor
from oncallpilot_runner.limits import (
    COOLDOWN_SECONDS,
    RATE_WINDOW_SECONDS,
    Limits,
    cooldown_key,
    limits_service,
    rate_key,
)
from oncallpilot_runner.request import parse
from oncallpilot_runner.signing import Keys, verify
from tests.conftest import NOW
from tests.fakes import FakeClock, FakeDockerClient, FakeStreams
from tests.publish_signed import build_request, entry, signed_for
from tests.test_execute import (  # noqa: F401 (fixtures)
    FakeCache,
    approved_for,
    cache,
    consumer,
    executor,
    failing_probes,
    ledger,
    names_of,
    record,
    results,
    run,
    signed_action,
    ticks,
)

REQUESTS = "ocp:runner:requests"
ACTIONS: list[tuple[str, dict[str, str | int]]] = [
    ("rollback_deploy", {"service": "api", "target_release": "1.4.0"}),
    ("restart_service", {"service": "api"}),
    ("scale_service", {"service": "api", "replicas": 2}),
    ("clear_cache", {"cache": "pricing"}),
]
IDS = [action for action, _ in ACTIONS]


def advance(clock: list[datetime], streams: FakeStreams, seconds: float) -> None:
    clock[0] += timedelta(seconds=seconds)
    streams.now_ms += int(seconds * 1000)


def signed_at(
    dry_runner: DryRunner, keys: Keys, action: str, params: Any, now: datetime
) -> dict[str, Any]:
    """A signed execute approved at `now`, carrying its own dry run's fingerprint."""
    dry = parse({**build_request("dry_run", action, params, now=now), "sig": "0" * 64})
    fingerprint = dry_runner.run(dry)["state_fingerprint"]
    return signed_for(
        build_request("execute", action, params, now=now, state_fingerprint=fingerprint), keys
    )


def another_deploy(ledger: Path) -> None:
    """A deploy lands after the dry run: api 1.5.0 now wants 2 replicas."""
    with ledger.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({**record("api", "1.5.0", "scale", "runner"), "replicas": 2}) + "\n")


def counted(streams: FakeStreams, service: str) -> int:
    return streams.zcount(rate_key(service), float("-inf"), float("inf"))


def pending(streams: FakeStreams) -> dict[str, Any]:
    return streams.pending[(REQUESTS, "runner")]


# --- limits.py: the counters ------------------------------------------------------------


@pytest.fixture
def limits(streams: FakeStreams, clock: list[datetime]) -> Limits:
    return Limits(streams, lambda: clock[0])


def test_a_fresh_service_has_both_gates_open(limits: Limits) -> None:
    assert limits.refusal("api") is None


def test_three_executions_in_30_minutes_close_the_rate_limit_per_service(
    limits: Limits, streams: FakeStreams, clock: list[datetime]
) -> None:
    for n in range(3):
        assert limits.refusal("api") is None
        limits.consume("api", f"execution-{n}")
        limits.finished("api")
        advance(clock, streams, COOLDOWN_SECONDS)  # the cooldown is over each time
    assert limits.refusal("api") == (
        "RATE_LIMITED", "3 executions on api in the last 30 min (limit 3)",
    )  # fmt: skip
    assert limits.refusal("worker") is None  # each service has its own budget
    # The first execution leaves the 30-minute window: one more is allowed.
    advance(clock, streams, RATE_WINDOW_SECONDS - 3 * COOLDOWN_SECONDS + 1)
    assert limits.refusal("api") is None


def test_the_cooldown_closes_the_service_for_120_s(
    limits: Limits, streams: FakeStreams, clock: list[datetime]
) -> None:
    limits.consume("api", "execution-1")
    assert limits.refusal("api") == ("COOLDOWN_ACTIVE", "api is cooling down for 120 s more")
    advance(clock, streams, COOLDOWN_SECONDS - 1)
    assert limits.refusal("api") == ("COOLDOWN_ACTIVE", "api is cooling down for 1 s more")
    advance(clock, streams, 1)
    assert limits.refusal("api") is None


def test_the_rate_limit_is_checked_before_the_cooldown(limits: Limits) -> None:
    for n in range(3):
        limits.consume("api", f"execution-{n}")
    closed = limits.refusal("api")
    assert closed is not None and closed[0] == "RATE_LIMITED"  # C5 order


def test_consume_prunes_old_timestamps_and_sets_both_ttls(
    limits: Limits, streams: FakeStreams
) -> None:
    streams.zadd(rate_key("api"), "long-ago", NOW.timestamp() - RATE_WINDOW_SECONDS - 1)
    limits.consume("api", "execution-1")
    assert streams.zsets[rate_key("api")] == {"execution-1": NOW.timestamp()}
    assert streams.expiries[rate_key("api")] == streams.now_ms + RATE_WINDOW_SECONDS * 1000
    assert streams.ttl(cooldown_key("api")) == COOLDOWN_SECONDS


def test_finished_starts_the_cooldown_again_from_the_end(
    limits: Limits, streams: FakeStreams, clock: list[datetime]
) -> None:
    limits.consume("api", "execution-1")
    advance(clock, streams, 100)  # a long action
    limits.finished("api")
    assert streams.ttl(cooldown_key("api")) == COOLDOWN_SECONDS


def test_clear_cache_counts_under_api() -> None:
    assert limits_service("clear_cache", {"cache": "pricing"}) == "api"
    assert limits_service("restart_service", {"service": "worker"}) == "worker"


# --- RUN-006 in the executor: a closed gate refuses before anything -------------------------


def close_gate(streams: FakeStreams, how: str, service: str) -> None:
    if how == "rate":
        for n in range(3):
            streams.zadd(rate_key(service), f"earlier-{n}", NOW.timestamp() - 60)
    else:
        streams.set_ex(cooldown_key(service), COOLDOWN_SECONDS)


@pytest.mark.parametrize(
    ("how", "code"), [("rate", "RATE_LIMITED"), ("cooldown", "COOLDOWN_ACTIVE")]
)
@pytest.mark.parametrize(("action", "params"), ACTIONS, ids=IDS)
def test_a_closed_gate_refuses_every_action_and_changes_nothing(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, cache: FakeCache,
    streams: FakeStreams, ledger: Path, action: str, params: Any, how: str, code: str,
) -> None:  # fmt: skip
    request = approved_for(dry_runner, action, params)
    close_gate(streams, how, limits_service(action, params))
    docker.calls.clear()
    ledger_before, counters_before = ledger.read_bytes(), deepcopy(streams.zsets)
    outcome, steps = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("refused", code)
    assert steps == []  # not even "validated"
    assert docker.calls == [] and cache.calls == []  # checked before the state is read
    assert ledger.read_bytes() == ledger_before
    assert streams.zsets == counters_before  # a refusal uses no budget


def test_a_rollback_is_not_exempt_from_the_cooldown_yet(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, streams: FakeStreams
) -> None:
    """§7.6 exempts rollback proposals, but nothing signed says a request is one
    (contract-review-a issue 12). Until that is decided, rollbacks cool down too."""
    request = approved_for(dry_runner, "rollback_deploy", ACTIONS[0][1])
    close_gate(streams, "cooldown", "api")
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("refused", "COOLDOWN_ACTIVE")
    assert docker.writes() == []


@pytest.mark.parametrize(("action", "params"), ACTIONS, ids=IDS)
def test_an_execution_that_ran_counts_once_and_starts_the_cooldown(
    executor: Executor, dry_runner: DryRunner, streams: FakeStreams, action: str, params: Any
) -> None:
    request = approved_for(dry_runner, action, params)
    outcome, _ = run(executor, request)
    assert outcome.status == "succeeded"
    assert streams.zsets == {rate_key("api"): {request.execution_id: NOW.timestamp()}}
    assert streams.ttl(cooldown_key("api")) == COOLDOWN_SECONDS


def test_clear_cache_closes_the_cooldown_for_api_actions(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    run(executor, approved_for(dry_runner, "clear_cache", {"cache": "catalog"}))
    outcome, _ = run(executor, approved_for(dry_runner, "restart_service", {"service": "api"}))
    assert (outcome.status, outcome.error_code) == ("refused", "COOLDOWN_ACTIVE")
    assert docker.writes() == []


def test_a_failed_execution_counts_too(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, streams: FakeStreams
) -> None:
    """It ran, and may have changed something: it uses the budget and cools down."""
    docker.fail.add(("restart", "cs-api-150-1"))
    request = approved_for(dry_runner, "restart_service", {"service": "api"})
    outcome, _ = run(executor, request)
    assert outcome.status == "failed"
    assert counted(streams, "api") == 1 and streams.exists(cooldown_key("api"))


def test_a_drift_abort_uses_no_budget(
    executor: Executor, dry_runner: DryRunner, streams: FakeStreams, ledger: Path
) -> None:
    request = approved_for(dry_runner, "restart_service", {"service": "api"})
    another_deploy(ledger)
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "STATE_DRIFT")
    assert streams.zsets == {} and not streams.exists(cooldown_key("api"))


def test_a_validation_abort_uses_no_budget(
    executor: Executor, dry_runner: DryRunner, streams: FakeStreams
) -> None:
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": 0})
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "VALIDATION_ERROR")
    assert streams.zsets == {} and not streams.exists(cooldown_key("api"))


def test_dry_runs_and_refused_requests_use_no_budget(
    consumer: Consumer, streams: FakeStreams, keys: Keys
) -> None:
    for action, params in ACTIONS:
        streams.add(REQUESTS, entry(signed_for(build_request("dry_run", action, params,
                                                             now=NOW), keys)))  # fmt: skip
        forged = {**build_request("execute", action, params, now=NOW), "sig": "7" * 64}
        streams.add(REQUESTS, entry(forged))
    consumer.poll()
    assert {(r["type"], r["status"]) for r in results(streams)} == {
        ("dry_run_result", "ok"), ("execution_result", "refused"),
    }  # fmt: skip
    assert streams.zsets == {}
    assert not any(streams.exists(cooldown_key(s)) for s in ("api", "worker", "redis"))


# --- RUN-006 through the consumer: signed refusals --------------------------------------------


def test_the_fourth_execution_in_30_minutes_is_refused_rate_limited(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    keys: Keys, clock: list[datetime],
) -> None:  # fmt: skip
    restart = ("restart_service", {"service": "api"})
    for _ in range(3):
        streams.add(REQUESTS, entry(signed_at(dry_runner, keys, *restart, clock[0])))
        consumer.poll()
        assert results(streams)[-1]["status"] == "succeeded"
        advance(clock, streams, COOLDOWN_SECONDS)  # cooled down, still inside 30 min
    writes = len(docker.writes())
    fourth = signed_at(dry_runner, keys, *restart, clock[0])
    streams.add(REQUESTS, entry(fourth))
    consumer.poll()
    final = results(streams)[-1]
    assert (final["type"], final["status"], final["error_code"]) == (
        "execution_result", "refused", "RATE_LIMITED",
    )  # fmt: skip
    assert final["execution_id"] == fourth["execution_id"] and verify(final, keys.link)
    assert final["health_after"] is None and final["captured"] is None
    assert len(docker.writes()) == writes  # nothing changed
    assert counted(streams, "api") == 3  # the refusal used no budget
    assert pending(streams) == {}


def test_a_second_execution_inside_120_s_is_refused_then_works_after_the_cooldown(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    keys: Keys, clock: list[datetime],
) -> None:  # fmt: skip
    restart = ("restart_service", {"service": "redis"})
    streams.add(REQUESTS, entry(signed_at(dry_runner, keys, *restart, clock[0])))
    consumer.poll()
    assert results(streams)[-1]["status"] == "succeeded"
    advance(clock, streams, COOLDOWN_SECONDS - 1)
    streams.add(REQUESTS, entry(signed_at(dry_runner, keys, *restart, clock[0])))
    consumer.poll()
    final = results(streams)[-1]
    assert (final["status"], final["error_code"]) == ("refused", "COOLDOWN_ACTIVE")
    assert verify(final, keys.link)
    assert docker.writes() == [("restart", "cs-redis")]
    advance(clock, streams, 1)
    streams.add(REQUESTS, entry(signed_at(dry_runner, keys, *restart, clock[0])))
    consumer.poll()
    assert results(streams)[-1]["status"] == "succeeded"
    assert docker.writes() == [("restart", "cs-redis"), ("restart", "cs-redis")]


# --- fail closed: Redis errors in the gate ---------------------------------------------------


def down(*_: Any) -> Any:
    raise RedisConnectionError("Redis is unreachable")


@pytest.mark.parametrize("command", ["zcount", "exists", "zadd", "set_ex"])
def test_a_redis_error_in_the_limits_changes_nothing_and_sends_no_result(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    keys: Keys, monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:  # fmt: skip
    """Reading the gate (zcount, exists) or counting the execution (zadd, set_ex)
    fails: the entry stays pending and nothing runs. Its redelivery is a
    duplicate (the ID is claimed), so it never runs later either; the worker's
    sweeper ends the execution with RUNNER_TIMEOUT."""
    message = signed_action(dry_runner, keys, "restart_service", {"service": "api"})
    monkeypatch.setattr(streams, command, down)
    streams.add(REQUESTS, entry(message))
    with pytest.raises(RedisConnectionError):
        consumer.poll()
    assert docker.writes() == [] and results(streams) == []
    assert len(pending(streams)) == 1  # not acknowledged
    monkeypatch.undo()  # Redis is back
    streams.now_ms += RECLAIM_IDLE_MS
    consumer.reclaim()
    assert docker.writes() == [] and results(streams) == []  # a duplicate: never runs
    assert pending(streams) == {}


def test_a_cooldown_that_cannot_be_renewed_keeps_the_one_set_at_the_start(
    executor: Executor, dry_runner: DryRunner, streams: FakeStreams,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:  # fmt: skip
    real, calls = streams.set_ex, []

    def renew_fails(key: str, ttl: int) -> None:
        calls.append(key)
        if len(calls) == 2:  # the renewal at the end
            down()
        real(key, ttl)

    monkeypatch.setattr(streams, "set_ex", renew_fails)
    with caplog.at_level(logging.WARNING, logger="runner.execute"):
        outcome, _ = run(executor, approved_for(dry_runner, "restart_service", {"service": "api"}))
    assert outcome.status == "succeeded"  # Docker already changed: the result still goes out
    assert streams.exists(cooldown_key("api"))
    assert "cooldown not renewed for api" in caplog.text


# --- RUN-005: idempotency ---------------------------------------------------------------------


def test_a_replay_during_the_run_never_touches_docker_again(
    consumer: Consumer, streams: FakeStreams, checker: Checker, dry_runner: DryRunner,
    executor: Executor, docker: FakeDockerClient, keys: Keys, ticks: FakeClock,
) -> None:  # fmt: skip
    """A second runner gets the replay while the first is in its health wait."""
    message = signed_action(dry_runner, keys, "restart_service", {"service": "api"})
    other = Consumer(streams, checker, "runner-b", heartbeat=None, dry_runner=dry_runner,
                     executor=executor, sleep=lambda _: None)  # fmt: skip
    replays: list[int] = []

    def replay(_: float) -> None:
        if not replays:
            streams.add(REQUESTS, entry(message))
            replays.append(other.poll())

    ticks.on_sleep.append(replay)
    streams.add(REQUESTS, entry(message))
    consumer.poll()
    assert replays == [1]  # runner-b read the replay mid-run
    assert docker.writes() == [("restart", "cs-api-150-1")]
    finals = [r for r in results(streams) if r["type"] == "execution_result"]
    assert [f["status"] for f in finals] == ["succeeded"]
    assert pending(streams) == {}


@pytest.mark.parametrize("fails", [False, True], ids=["after-success", "after-failure"])
def test_a_replay_after_the_run_is_ignored(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    keys: Keys, clock: list[datetime], fails: bool,
) -> None:  # fmt: skip
    if fails:
        docker.fail.add(("restart", "cs-api-150-1"))
    message = signed_action(dry_runner, keys, "restart_service", {"service": "api"})
    streams.add(REQUESTS, entry(message))
    consumer.poll()
    first = results(streams)
    assert first[-1]["status"] == ("failed" if fails else "succeeded")
    advance(clock, streams, 30)  # later, still before expires_at
    streams.add(REQUESTS, entry(message))
    consumer.poll()
    assert results(streams) == first  # no second result
    assert docker.writes() == [("restart", "cs-api-150-1")]  # one attempt only
    assert pending(streams) == {}


def test_a_replay_after_a_runner_restart_is_ignored(
    streams: FakeStreams, checker: Checker, dry_runner: DryRunner, executor: Executor,
    docker: FakeDockerClient, keys: Keys, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """The runner dies after acting but before the acknowledgement. The restarted
    runner (same hostname, so the same consumer name) re-reads its pending entry,
    and a fresh copy of the message arrives too: neither runs."""

    class Crash(Exception):
        pass

    message = signed_action(dry_runner, keys, "restart_service", {"service": "api"})
    first = Consumer(streams, checker, "runner-1", heartbeat=None, dry_runner=dry_runner,
                     executor=executor, sleep=lambda _: None)  # fmt: skip
    first.ensure_group()

    def crash(*_: Any) -> None:
        raise Crash

    monkeypatch.setattr(streams, "ack", crash)
    streams.add(REQUESTS, entry(message))
    with pytest.raises(Crash):
        first.poll()
    assert docker.writes() == [("restart", "cs-api-150-1")]
    monkeypatch.undo()
    restarted = Consumer(streams, checker, "runner-1", heartbeat=None, dry_runner=dry_runner,
                         executor=executor, sleep=lambda _: None)  # fmt: skip
    assert restarted.recover() == 1
    streams.add(REQUESTS, entry(message))
    restarted.poll()
    assert docker.writes() == [("restart", "cs-api-150-1")]
    assert [r["type"] for r in results(streams)].count("execution_result") == 1
    assert pending(streams) == {}


def test_a_claim_that_cannot_be_written_fails_closed(
    consumer: Consumer, streams: FakeStreams, checker: Checker, dry_runner: DryRunner,
    docker: FakeDockerClient, keys: Keys,
) -> None:  # fmt: skip
    """SET NX fails: nothing runs, no result, the entry stays pending. If Redis
    did store the key and only the reply was lost, the retry is a duplicate."""
    message = signed_action(dry_runner, keys, "restart_service", {"service": "api"})
    real = checker.set_nx

    def stored_then_lost(key: str, ttl: int) -> bool:
        real(key, ttl)
        raise RedisConnectionError("reply lost")

    checker.set_nx = stored_then_lost
    docker.calls.clear()
    streams.add(REQUESTS, entry(message))
    with pytest.raises(RedisConnectionError):
        consumer.poll()
    assert docker.calls == [] and results(streams) == [] and len(pending(streams)) == 1
    checker.set_nx = real
    streams.now_ms += RECLAIM_IDLE_MS
    consumer.reclaim()
    assert docker.writes() == [] and results(streams) == [] and pending(streams) == {}


# --- RUN-008: drift between the dry run and the execute ----------------------------------------


@pytest.mark.parametrize(("action", "params"), ACTIONS[:3], ids=IDS[:3])
def test_a_state_change_after_the_dry_run_aborts_with_state_drift(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    keys: Keys, ledger: Path, action: str, params: Any,
) -> None:  # fmt: skip
    message = signed_action(dry_runner, keys, action, params)  # the dry run's fingerprint
    another_deploy(ledger)
    ledger_before = ledger.read_bytes()
    streams.add(REQUESTS, entry(message))
    consumer.poll()
    (final,) = results(streams)  # no progress step: nothing was validated
    assert (final["type"], final["status"], final["error_code"]) == (
        "execution_result", "aborted", "STATE_DRIFT",
    )  # fmt: skip
    assert verify(final, keys.link)
    assert docker.writes() == [] and ledger.read_bytes() == ledger_before
    assert streams.zsets == {}


def test_clear_cache_has_no_container_state_so_only_a_wrong_fingerprint_drifts(
    executor: Executor, dry_runner: DryRunner, cache: FakeCache
) -> None:
    """Its fingerprint covers no containers (active release and replicas null, no
    targets), so a deploy cannot change it; a fingerprint from elsewhere still aborts."""
    request = approved_for(dry_runner, "clear_cache", {"cache": "pricing"})
    stale = parse({**build_request("execute", "clear_cache", {"cache": "pricing"}, now=NOW,
                                   state_fingerprint="0" * 64), "sig": "0" * 64})  # fmt: skip
    outcome, _ = run(executor, stale)
    assert (outcome.status, outcome.error_code) == ("aborted", "STATE_DRIFT")
    assert cache.calls == []
    assert run(executor, request)[0].status == "succeeded"


def test_container_state_alone_is_not_drift(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    """§7.5, C8: state, health and restart counts are excluded from the fingerprint."""
    request = approved_for(dry_runner, "restart_service", {"service": "api"})
    c = docker.containers["cs-api-150-1"]
    docker.containers["cs-api-150-1"] = type(c)(
        **{**c.__dict__, "state": "exited", "health": "none", "restart_count": 7}
    )
    outcome, _ = run(executor, request)
    assert outcome.status == "succeeded"
    assert docker.writes() == [("start", "cs-api-150-1")]


@pytest.mark.parametrize(("action", "params"), ACTIONS[:3], ids=IDS[:3])
def test_the_fingerprint_is_read_after_the_gate_right_before_the_first_change(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, streams: FakeStreams,
    ledger: Path, monkeypatch: pytest.MonkeyPatch, action: str, params: Any,
) -> None:  # fmt: skip
    """A deploy that lands while the gate is checked is still caught: the state
    is read after the gate, and nothing is written before it."""
    request = approved_for(dry_runner, action, params)
    gate = executor.limits.refusal

    def gate_then_deploy(service: str) -> tuple[str, str] | None:
        closed = gate(service)
        another_deploy(ledger)
        return closed

    monkeypatch.setattr(executor.limits, "refusal", gate_then_deploy)
    outcome, steps = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "STATE_DRIFT")
    assert steps == [] and docker.writes() == [] and streams.zsets == {}


@pytest.mark.parametrize(("action", "params"), ACTIONS, ids=IDS)
def test_no_docker_write_comes_before_the_state_is_read_again(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, action: str,
    params: Any,
) -> None:  # fmt: skip
    request = approved_for(dry_runner, action, params)
    docker.calls.clear()
    run(executor, request)
    first_write = next((i for i, c in enumerate(docker.calls) if c in docker.writes()), None)
    reads = [i for i, (call, _) in enumerate(docker.calls) if call == "list"]
    assert reads and (first_write is None or reads[0] < first_write)


# --- RUN-014: every action ends with the structural check ------------------------------------

BROKEN_AFTER = {  # the container the structural check covers, per action
    "rollback_deploy": "cs-api-140-1",
    "restart_service": "cs-api-150-1",
    "scale_service": "cs-api-150-2",
    "clear_cache": "cs-redis",
}


@pytest.mark.parametrize(("action", "params"), ACTIONS, ids=IDS)
def test_a_failed_structural_check_fails_every_action_and_keeps_its_steps(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    keys: Keys, action: str, params: Any,
) -> None:  # fmt: skip
    message = signed_action(dry_runner, keys, action, params)
    broken = BROKEN_AFTER[action]
    real_emit = consumer._publish

    def publish(result: dict[str, Any], **kw: Any) -> None:
        step = result.get("step") or {}
        if (step.get("name"), step.get("status")) == ("structural health check", "running"):
            c = docker.containers[broken]  # it goes unhealthy as the check starts
            docker.containers[broken] = type(c)(**{**c.__dict__, "health": "unhealthy"})
        real_emit(result, **kw)

    consumer._publish = publish  # type: ignore[method-assign]
    streams.add(REQUESTS, entry(message))
    consumer.poll()
    *steps, final = results(streams)
    assert (final["status"], final["error_code"]) == ("failed", None)
    structural = final["health_after"]["structural"]
    assert structural["passed"] is False
    assert broken in [c["name"] for c in structural["containers"]]
    names = [(s["step"]["name"], s["step"]["status"]) for s in steps]
    assert names[0] == ("validated", "succeeded")
    assert names[-2:] == [("structural health check", "running"), ("done", "failed")]
    assert all(verify(r, keys.link) for r in (*steps, final))
