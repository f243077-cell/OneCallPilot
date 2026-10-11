"""rollback_deploy execute and the structural health check (A2.4a: RUN-008, RUN-011,
RUN-013, RUN-014, RUN-015; idempotency RUN-005).

The fake Docker records every call in order, so each test states the exact
inspects and writes; the ledger is a real file with C6 records.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from oncallpilot_runner import ledger as ledger_module
from oncallpilot_runner.checks import Checker, Duplicate, Refused
from oncallpilot_runner.consumer import RESULTS_STREAM, Consumer, Executed
from oncallpilot_runner.dry_run import DryRunner
from oncallpilot_runner.execute import Executor, Outcome
from oncallpilot_runner.health import LB_PROBE, HealthChecker, probe_urls
from oncallpilot_runner.limits import Limits
from oncallpilot_runner.request import Request, parse
from oncallpilot_runner.shop_admin import CacheClearFailed, CacheStatsUnavailable
from oncallpilot_runner.signing import Keys, verify
from oncallpilot_runner.targets import Targets
from tests.conftest import NOW, TARGETS
from tests.fakes import FakeClock, FakeDockerClient, FakeStreams
from tests.publish_signed import build_request, entry, signed_for

SHA = {"1.4.0": "6" * 40, "1.5.0": "e" * 40, "2.1.0": "2" * 40}


def record(service: str, release: str, kind: str = "deploy", by: str = "ci") -> dict[str, Any]:
    return {
        "deploy_id": "00000000-0000-4000-8000-00000000000" + release[-1],
        "kind": kind, "service": service, "release": release, "commit_sha": SHA[release],
        "commit_message": f"release {release}", "image_tag": f"chaos-shop/{service}:{release}",
        "config_hash": "c" * 64, "replicas": 1, "deployed_at": "2026-10-10T09:00:00Z",
        "deployed_by": by, "reason": "scheduled release",
    }  # fmt: skip


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    """Overrides conftest's: full C6 records; api 1.5.0 is active after 1.4.0."""
    path = tmp_path / "deploys.jsonl"
    lines = [record("api", "1.4.0", by="setup"), record("worker", "2.1.0", by="setup"),
             record("api", "1.5.0")]  # fmt: skip
    path.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def ticks() -> FakeClock:
    return FakeClock()


@pytest.fixture
def failing_probes() -> set[str]:
    return set()


class FakeCache:
    """The cache clear endpoint: records each call; `answer` or `error` scripts it."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.answer = True
        self.error: Exception | None = None

    def __call__(self, cache: str) -> bool:
        self.calls.append(cache)
        if self.error is not None:
            raise self.error
        return self.answer


@pytest.fixture
def cache() -> FakeCache:
    return FakeCache()


@pytest.fixture
def executor(
    docker: FakeDockerClient,
    dry_runner: DryRunner,
    ledger: Path,
    ticks: FakeClock,
    failing_probes: set[str],
    cache: FakeCache,
    streams: FakeStreams,
    clock: list[datetime],
) -> Executor:
    def probe(url: str) -> dict[str, Any]:
        ok = url not in failing_probes
        return {"url": url, "ok": ok, "status_code": 200 if ok else 503,
                "latency_ms": 1.0, "error": None if ok else "HTTP 503"}  # fmt: skip

    targets = Targets.load(TARGETS)
    health = HealthChecker(docker, targets, probe, lambda: NOW, ticks.monotonic, ticks.sleep)
    limits = Limits(streams, lambda: clock[0])
    return Executor(
        docker, dry_runner, health, targets, ledger, lambda: NOW, limits, cache_clear=cache
    )


def approved(dry_runner: DryRunner, target: str = "1.4.0", **overrides: Any) -> Request:
    """An execute carrying the fingerprint of a dry run of the same action."""
    params: dict[str, str | int] = {"service": "api", "target_release": target}
    preview = dry_runner.run(
        parse({**build_request("dry_run", params=params, now=NOW), "sig": "0" * 64})
    )
    message = build_request(params=params, now=NOW, state_fingerprint=preview["state_fingerprint"])
    return parse({**message, **overrides, "sig": "0" * 64})


Steps = list[tuple[str, str, str]]


def run(executor: Executor, request: Request) -> tuple[Outcome, Steps]:
    steps: Steps = []
    outcome = executor.execute(request, lambda n, s, m: steps.append((n, s, m)))
    return outcome, steps


def ledger_lines(ledger: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]


# --- the successful rollback (scenario 2: api 1.5.0 -> 1.4.0) ------------------------


def test_a_rollback_starts_the_target_waits_then_stops_the_old_release(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    outcome, steps = run(executor, approved(dry_runner))
    assert outcome.status == "succeeded" and outcome.error_code is None
    # The exact Docker writes, in order, each right after a fresh inspect.
    assert docker.actions() == [
        ("inspect", "cs-api-140-1"), ("start", "cs-api-140-1"),
        ("inspect", "cs-api-150-1"), ("stop", "cs-api-150-1"),
    ]  # fmt: skip
    assert (docker.containers["cs-api-140-1"].state, docker.containers["cs-api-150-1"].state) == (
        "running", "exited",
    )  # fmt: skip
    assert [(name, status) for name, status, _ in steps] == [
        ("validated", "succeeded"),
        ("starting cs-api-140-1", "succeeded"),
        ("healthy cs-api-140-1", "running"),
        ("healthy cs-api-140-1", "succeeded"),
        ("stopping cs-api-150-1", "succeeded"),
        ("recording the rollback in the deploy ledger", "succeeded"),
        ("structural health check", "running"),
        ("done", "succeeded"),
    ]
    assert outcome.captured == {"previous_release": "1.5.0"}
    assert outcome.structural is not None and outcome.structural["passed"] is True
    assert outcome.structural["containers"] == [
        {"name": "cs-api-140-1", "state": "running", "health": "healthy"}
    ]
    assert [p["url"] for p in outcome.structural["probes"]] == [
        "http://cs-api-140-1:8000/healthz", LB_PROBE,
    ]  # fmt: skip


def test_the_rollback_appends_a_valid_runner_record(
    executor: Executor, dry_runner: DryRunner, ledger: Path
) -> None:
    before = ledger_lines(ledger)
    run(executor, approved(dry_runner))
    *same, added = ledger_lines(ledger)
    assert same == before
    assert {k: added[k] for k in ("kind", "service", "release", "replicas", "deployed_by")} == {
        "kind": "rollback", "service": "api", "release": "1.4.0", "replicas": 1,
        "deployed_by": "runner",
    }  # fmt: skip
    assert added["commit_sha"] == SHA["1.4.0"] and added["image_tag"] == "chaos-shop/api:1.4.0"
    assert added["config_hash"] == "c" * 64 and added["deployed_at"] == "2026-10-14T09:01:59Z"
    assert added["reason"] == "rolled back to release 1.4.0"  # neutral (TB-010)
    assert ledger_module.active(ledger, "api") == ledger_module.Active("1.4.0", 1)


def test_the_structural_check_waits_for_stable_restart_counts(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ticks: FakeClock
) -> None:
    """A container that restarts during the 10 s window does not pass on that sample."""
    windows = {"n": 0}

    def bump(now: float) -> None:
        windows["n"] += 1
        if windows["n"] == 1:  # the container restarts during the first 10 s window
            c = docker.containers["cs-api-140-1"]
            docker.containers["cs-api-140-1"] = type(c)(**{**c.__dict__, "restart_count": 1})

    ticks.on_sleep.append(bump)
    outcome, _ = run(executor, approved(dry_runner))
    assert outcome.status == "succeeded"
    assert ticks.slept.count(10.0) >= 2  # needed a second stability window


# --- drift, and refusals that never execute -----------------------------------------


def test_a_changed_state_aborts_with_state_drift_and_changes_nothing(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    request = approved(dry_runner)
    with ledger.open("a", encoding="utf-8", newline="\n") as f:  # a scale since the dry run
        f.write(json.dumps({**record("api", "1.5.0", "scale", "runner"), "replicas": 2}) + "\n")
    before = ledger.read_bytes()
    outcome, steps = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "STATE_DRIFT")
    assert docker.writes() == [] and steps == [] and ledger.read_bytes() == before


def test_a_missing_target_slot_is_drift(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    request = approved(dry_runner)
    del docker.containers["cs-api-140-1"]
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "STATE_DRIFT")
    assert docker.writes() == []


def test_an_unreadable_state_aborts(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    request = approved(dry_runner)
    docker.unavailable = True
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "VALIDATION_ERROR")


def test_missing_slots_approved_anyway_are_refused_before_any_change(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    del docker.containers["cs-api-140-1"]
    outcome, _ = run(executor, approved(dry_runner))  # the dry run already showed it
    assert (outcome.status, outcome.error_code) == ("aborted", "VALIDATION_ERROR")
    assert docker.writes() == []


def test_an_action_without_an_execute_handler_changes_nothing(
    executor: Executor, docker: FakeDockerClient
) -> None:
    executor.handlers = {}
    request = parse({**build_request(action="restart_service", params={"service": "api"},
                                     now=NOW), "sig": "0" * 64})  # fmt: skip
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "ACTION_NOT_ALLOWED")
    assert docker.calls == []


# --- the new release fails: the old one keeps running --------------------------------


@pytest.mark.parametrize("after_start", [("running", "unhealthy"), ("exited", "none"),
                                         ("restarting", "starting")])  # fmt: skip
def test_a_new_release_that_never_gets_healthy_is_undone(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path,
    ticks: FakeClock, after_start: tuple[str, str],
) -> None:  # fmt: skip
    docker.on_start["cs-api-140-1"] = after_start
    before = ledger.read_bytes()
    outcome, steps = run(executor, approved(dry_runner))
    assert outcome.status == "failed" and outcome.error_code is None
    assert docker.writes() == [("start", "cs-api-140-1"), ("stop", "cs-api-140-1")]
    assert docker.containers["cs-api-150-1"].state == "running"  # never touched
    assert ledger.read_bytes() == before  # the active release did not change
    assert outcome.captured == {"previous_release": "1.5.0"}
    assert outcome.structural is not None and outcome.structural["passed"] is False
    assert ("healthy cs-api-140-1", "failed") in [(n, s) for n, s, _ in steps]
    assert 60 <= ticks.t < 70  # gave up after the 60 s limit


def test_a_failing_probe_also_keeps_the_old_release(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient,
    failing_probes: set[str],
) -> None:  # fmt: skip
    failing_probes.add("http://cs-api-140-1:8000/healthz")
    outcome, steps = run(executor, approved(dry_runner))
    assert outcome.status == "failed"
    assert docker.containers["cs-api-150-1"].state == "running"
    failed = [m for n, s, m in steps if s == "failed"]
    assert failed == ["http://cs-api-140-1:8000/healthz HTTP 503"]


def test_a_start_failure_with_three_replicas_undoes_the_started_slots(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    with ledger.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({**record("api", "1.5.0", "scale", "runner"), "replicas": 3}) + "\n")
    for replica in (2, 3):
        docker.add(f"cs-api-150-{replica}", "api", "1.5.0", replica, "running", "healthy")
    docker.fail.add(("start", "cs-api-140-2"))
    outcome, _ = run(executor, approved(dry_runner))
    assert outcome.status == "failed"
    assert docker.writes() == [
        ("start", "cs-api-140-1"), ("start", "cs-api-140-2"), ("stop", "cs-api-140-1"),
    ]  # fmt: skip
    assert all(docker.containers[f"cs-api-150-{r}"].state == "running" for r in (1, 2, 3))


def test_a_container_that_fails_the_allowlist_at_write_time_is_never_touched(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    request = approved(dry_runner)
    real = docker.inspect
    docker.calls.clear()

    def relabelled(name: str) -> Any:
        c = real(name)
        return type(c)(**{**c.__dict__, "labels": {**c.labels, "oncallpilot.managed": "no"}})

    docker.inspect = relabelled  # type: ignore[method-assign]
    outcome, _ = run(executor, request)
    assert outcome.status == "failed" and docker.writes() == []


# --- after the switch ------------------------------------------------------------------


def test_a_failed_structural_check_after_the_switch_reports_failed(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path,
    failing_probes: set[str], ticks: FakeClock,
) -> None:  # fmt: skip
    def lb_breaks(now: float) -> None:
        if docker.containers["cs-api-150-1"].state == "exited":
            failing_probes.add(LB_PROBE)

    ticks.on_sleep.append(lb_breaks)
    docker.on_start["cs-api-140-1"] = ("running", "healthy")
    outcome, steps = run(executor, approved(dry_runner))
    # Healthy at the first sample (before any sleep), then cs-lb fails after the stop.
    assert outcome.status == "failed"
    assert outcome.structural is not None and outcome.structural["passed"] is False
    assert steps[-1][:2] == ("done", "failed")
    assert ledger_lines(ledger)[-1]["kind"] == "rollback"  # the switch did happen


def test_a_ledger_that_cannot_be_appended_fails_but_keeps_the_new_release(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    def broken(path: Path, record: dict[str, Any]) -> None:
        raise ledger_module.LedgerError("cannot append to the deploy ledger: read-only")

    monkeypatch.setattr(ledger_module, "append", broken)
    outcome, steps = run(executor, approved(dry_runner))
    assert outcome.status == "failed"
    assert docker.containers["cs-api-140-1"].state == "running"  # never stopped again
    assert steps[-1][1] == "failed"


def test_a_stop_failure_reports_failed_without_stopping_the_new_release(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    docker.fail.add(("stop", "cs-api-150-1"))
    outcome, _ = run(executor, approved(dry_runner))
    assert outcome.status == "failed"
    assert docker.containers["cs-api-140-1"].state == "running"


# --- the ledger writer (C6) -------------------------------------------------------------


def test_append_never_creates_the_ledger(tmp_path: Path) -> None:
    missing = tmp_path / "deploys.jsonl"
    with pytest.raises(ledger_module.LedgerError, match="cannot open"):
        ledger_module.append(missing, {"x": 1})
    assert not missing.exists()


def test_a_record_that_would_break_c6_is_not_written(ledger: Path) -> None:
    with ledger.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({**record("api", "1.4.0"), "commit_sha": "nope"}) + "\n")
    with pytest.raises(ledger_module.LedgerError, match="commit_sha is 40 hex"):
        ledger_module.runner_record(ledger, kind="rollback", service="api", release="1.4.0",
                                    replicas=1, reason="r", now=NOW)  # fmt: skip
    with pytest.raises(ledger_module.LedgerError, match="no record to copy"):
        ledger_module.runner_record(ledger, kind="rollback", service="api", release="9.9.9",
                                    replicas=1, reason="r", now=NOW)  # fmt: skip


def test_probe_urls() -> None:
    assert probe_urls("api", ["cs-api-140-2", "cs-api-140-1"]) == [
        "http://cs-api-140-1:8000/healthz", "http://cs-api-140-2:8000/healthz", LB_PROBE,
    ]  # fmt: skip
    assert probe_urls("worker", ["cs-worker-210"]) == ["http://cs-worker-210:8001/healthz"]
    assert probe_urls("redis", ["cs-redis"]) == []


# --- through the consumer: signed progress and result, refusals, replays ---------------


@pytest.fixture
def consumer(
    streams: FakeStreams, checker: Checker, dry_runner: DryRunner, executor: Executor
) -> Consumer:
    c = Consumer(streams, checker, "runner-a", heartbeat=None, dry_runner=dry_runner,
                 executor=executor, sleep=lambda _: None)  # fmt: skip
    c.ensure_group()
    return c


def results(streams: FakeStreams) -> list[dict[str, Any]]:
    return [json.loads(f["msg"]) for _, f in streams.streams.get(RESULTS_STREAM, [])]


def signed_execute(dry_runner: DryRunner, keys: Keys) -> dict[str, Any]:
    request = approved(dry_runner)
    message = build_request(params=dict(request.params), now=NOW,
                            state_fingerprint=request.state_fingerprint)  # fmt: skip
    return signed_for(message, keys)


def test_an_execute_publishes_signed_progress_then_one_result(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, keys: Keys
) -> None:
    message = signed_execute(dry_runner, keys)
    streams.add("ocp:runner:requests", entry(message))
    consumer.poll()
    *steps, final = results(streams)
    assert all(verify(r, keys.link) and not verify(r, keys.action) for r in (*steps, final))
    assert {(r["type"], r["status"]) for r in steps} == {("progress", "ok")}
    assert all(r["execution_id"] == message["execution_id"] for r in (*steps, final))
    assert [r["step"]["name"] for r in steps][0] == "validated"
    assert (final["type"], final["status"], final["error_code"]) == (
        "execution_result", "succeeded", None,
    )  # fmt: skip
    assert final["health_after"]["structural"]["passed"] is True
    assert final["captured"] == {"previous_release": "1.5.0"}
    assert final["dry_run"] is None and final["step"] is None
    assert streams.pending[("ocp:runner:requests", "runner")] == {}


def test_a_replayed_execute_does_nothing(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    keys: Keys,
) -> None:  # fmt: skip
    message = signed_execute(dry_runner, keys)
    streams.add("ocp:runner:requests", entry(message))
    streams.add("ocp:runner:requests", entry(message))
    entries = streams.read_group("runner", "runner-a", "ocp:runner:requests", ">", 10, None)
    decisions = [consumer.handle(i, f) for i, f in entries]
    assert [type(d) for d in decisions] == [Executed, Duplicate]
    assert docker.writes() == [("start", "cs-api-140-1"), ("stop", "cs-api-150-1")]
    assert [r["type"] for r in results(streams)].count("execution_result") == 1


@pytest.mark.parametrize(
    "spoil",
    [
        lambda m, k: {**m, "sig": "6" * 64},  # forged
        lambda m, k: signed_for({**m, "approved_by": None}, k),  # no approver
        lambda m, k: signed_for(
            {**m, "params": {"service": "api; id", "target_release": "1.4.0"}}, k
        ),  # free-form target
        lambda m, k: signed_for({**m, "expires_at": "2026-10-14T09:00:00Z",
                                 "issued_at": "2026-10-14T08:59:00Z"}, k),  # expired
        lambda m, k: {**m, "sig": signed_for({**m, "type": "dry_run"}, k)["sig"]},  # link key
    ],
)  # fmt: skip
def test_refused_executes_never_reach_docker_writes(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    keys: Keys, spoil: Any,
) -> None:  # fmt: skip
    good = signed_execute(dry_runner, keys)
    unsigned = {k: v for k, v in good.items() if k != "sig"}
    streams.add("ocp:runner:requests", entry(spoil(unsigned, keys)))
    docker.calls.clear()
    (entry_id, fields), *_ = streams.read_group("runner", "runner-a", "ocp:runner:requests",
                                               ">", 1, None)  # fmt: skip
    decision = consumer.handle(entry_id, fields)
    assert isinstance(decision, Refused | Duplicate)
    assert docker.calls == []
    assert all(r["status"] == "refused" for r in results(streams))


def test_a_bug_during_the_execution_still_answers_failed(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, executor: Executor,
    keys: Keys, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    def boom(*_: Any) -> Outcome:
        raise RuntimeError("bug")

    monkeypatch.setattr(executor, "execute", boom)
    streams.add("ocp:runner:requests", entry(signed_execute(dry_runner, keys)))
    consumer.poll()
    (final,) = results(streams)
    assert (final["status"], final["error_code"]) == ("failed", None)


def test_an_aborted_result_carries_only_the_error_code(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, keys: Keys, ledger: Path
) -> None:
    message = signed_execute(dry_runner, keys)
    with ledger.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({**record("api", "1.5.0", "scale", "runner"), "replicas": 2}) + "\n")
    streams.add("ocp:runner:requests", entry(message))
    consumer.poll()
    (final,) = results(streams)
    assert (final["status"], final["error_code"]) == ("aborted", "STATE_DRIFT")
    assert final["health_after"] is None and final["captured"] is None


def test_the_final_result_is_retried_and_progress_failures_are_only_logged(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, keys: Keys
) -> None:
    real_add, calls = streams.add, {"n": 0}

    def flaky(stream: str, fields: dict[str, str]) -> str:
        if stream == RESULTS_STREAM:
            calls["n"] += 1
            kind = json.loads(fields["msg"])["type"]
            if kind == "progress" and calls["n"] == 2:
                raise RedisConnectionError("blip")  # one progress step lost
            if kind == "execution_result" and calls["n"] < 12:
                raise RedisConnectionError("blip")  # the final result: retried
        return real_add(stream, fields)

    streams.add = flaky  # type: ignore[method-assign]
    streams.add("ocp:runner:requests", entry(signed_execute(dry_runner, keys)))
    consumer.poll()
    kinds = [r["type"] for r in results(streams)]
    assert kinds.count("execution_result") == 1 and kinds.count("progress") == 7


def test_the_final_result_gives_up_after_its_retries(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, keys: Keys
) -> None:
    real_add = streams.add

    def down(stream: str, fields: dict[str, str]) -> str:
        if stream == RESULTS_STREAM and json.loads(fields["msg"])["type"] == "execution_result":
            raise RedisConnectionError("down")
        return real_add(stream, fields)

    streams.add = down  # type: ignore[method-assign]
    streams.add("ocp:runner:requests", entry(signed_execute(dry_runner, keys)))
    with pytest.raises(RedisConnectionError):
        consumer.poll()
    assert len(streams.pending[("ocp:runner:requests", "runner")]) == 1  # not acknowledged


def test_steps_carry_timestamps(executor: Executor) -> None:
    assert isinstance(executor.now(), datetime)


# === A2.4b: restart_service, scale_service, clear_cache ===============================


def approved_for(dry_runner: DryRunner, action: str, params: dict[str, str | int]) -> Request:
    """An execute of any action, carrying the fingerprint of its own dry run."""
    dry = parse({**build_request("dry_run", action, params, now=NOW), "sig": "0" * 64})
    fingerprint = dry_runner.run(dry)["state_fingerprint"]
    message = build_request("execute", action, params, now=NOW, state_fingerprint=fingerprint)
    return parse({**message, "sig": "0" * 64})


def names_of(steps: Steps) -> list[tuple[str, str]]:
    return [(name, status) for name, status, _ in steps]


def set_api_replicas(ledger: Path, docker: FakeDockerClient, replicas: int) -> None:
    """The ledger says api 1.5.0 runs `replicas`, and that many slots are running."""
    with ledger.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({**record("api", "1.5.0", "scale", "runner"), "replicas": replicas})
                + "\n")  # fmt: skip
    for replica in range(1, replicas + 1):
        docker.add(f"cs-api-150-{replica}", "api", "1.5.0", replica, "running", "healthy")


# --- restart_service (RUN-009) --------------------------------------------------------


def test_restart_restarts_the_active_release_and_writes_no_record(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    before = ledger.read_bytes()
    outcome, steps = run(executor, approved_for(dry_runner, "restart_service", {"service": "api"}))
    assert (outcome.status, outcome.captured) == ("succeeded", {})
    assert docker.actions() == [("inspect", "cs-api-150-1"), ("restart", "cs-api-150-1")]
    assert names_of(steps) == [
        ("validated", "succeeded"),
        ("restarting cs-api-150-1", "succeeded"),
        ("healthy cs-api-150-1", "running"),
        ("healthy cs-api-150-1", "succeeded"),
        ("structural health check", "running"),
        ("done", "succeeded"),
    ]
    assert ledger.read_bytes() == before  # §2.10: only rollback and scale write records


def test_restart_starts_an_exited_container(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    docker.add("cs-worker-210", "worker", "2.1.0", 1, state="exited")
    request = approved_for(dry_runner, "restart_service", {"service": "worker"})
    outcome, steps = run(executor, request)
    assert outcome.status == "succeeded"
    assert docker.actions() == [("inspect", "cs-worker-210"), ("start", "cs-worker-210")]
    assert ("starting cs-worker-210", "succeeded") in names_of(steps)
    assert [p["url"] for p in outcome.structural["probes"]] == [  # type: ignore[index]
        "http://cs-worker-210:8001/healthz"
    ]


def test_restart_of_redis_uses_no_ledger_values_and_no_probe(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    before = ledger.read_bytes()
    outcome, _ = run(executor, approved_for(dry_runner, "restart_service", {"service": "redis"}))
    assert outcome.status == "succeeded"
    assert docker.actions() == [("inspect", "cs-redis"), ("restart", "cs-redis")]
    assert outcome.structural is not None and outcome.structural["probes"] == []
    assert ledger.read_bytes() == before


def test_restart_goes_one_at_a_time(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    """Ruled 2026-10-11: each replica passes health before the next is restarted."""
    set_api_replicas(ledger, docker, 3)
    outcome, steps = run(executor, approved_for(dry_runner, "restart_service", {"service": "api"}))
    assert outcome.status == "succeeded"
    assert docker.writes() == [("restart", f"cs-api-150-{r}") for r in (1, 2, 3)]
    order = [n for n, s in names_of(steps) if s == "succeeded"]
    assert order[1:7] == [
        "restarting cs-api-150-1", "healthy cs-api-150-1",
        "restarting cs-api-150-2", "healthy cs-api-150-2",
        "restarting cs-api-150-3", "healthy cs-api-150-3",
    ]  # fmt: skip


def test_a_restart_that_never_gets_healthy_stops_the_rolling_restart(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    set_api_replicas(ledger, docker, 3)
    docker.on_start["cs-api-150-2"] = ("running", "unhealthy")
    outcome, steps = run(executor, approved_for(dry_runner, "restart_service", {"service": "api"}))
    assert outcome.status == "failed"
    assert docker.writes() == [("restart", "cs-api-150-1"), ("restart", "cs-api-150-2")]
    assert docker.containers["cs-api-150-3"].state == "running"  # never touched
    assert ("healthy cs-api-150-2", "failed") in names_of(steps)
    assert outcome.structural is not None and outcome.structural["passed"] is False


def test_a_restart_call_that_fails_stops_there(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    set_api_replicas(ledger, docker, 2)
    docker.fail.add(("restart", "cs-api-150-1"))
    outcome, steps = run(executor, approved_for(dry_runner, "restart_service", {"service": "api"}))
    assert outcome.status == "failed"
    assert docker.writes() == [("restart", "cs-api-150-1")]  # attempted, refused; no more
    assert ("restarting cs-api-150-1", "failed") in names_of(steps)


def test_a_restart_after_drift_changes_nothing(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    request = approved_for(dry_runner, "restart_service", {"service": "api"})
    del docker.containers["cs-api-150-1"]
    outcome, steps = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "STATE_DRIFT")
    assert docker.writes() == [] and steps == []


# --- scale_service (RUN-010) ----------------------------------------------------------


def test_scale_up_starts_slots_waits_and_records_the_new_count(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": 3})
    outcome, steps = run(executor, request)
    assert (outcome.status, outcome.captured) == ("succeeded", {"previous_replicas": 1})
    assert docker.actions() == [
        ("inspect", "cs-api-150-1"),  # already running: no write
        ("inspect", "cs-api-150-2"), ("start", "cs-api-150-2"),
        ("inspect", "cs-api-150-3"), ("start", "cs-api-150-3"),
    ]  # fmt: skip
    assert names_of(steps)[-4:] == [
        ("healthy cs-api-150-1, cs-api-150-2, cs-api-150-3", "succeeded"),
        ("recording the scale in the deploy ledger", "succeeded"),
        ("structural health check", "running"),
        ("done", "succeeded"),
    ]
    added = ledger_lines(ledger)[-1]
    assert {k: added[k] for k in ("kind", "release", "replicas", "deployed_by", "reason")} == {
        "kind": "scale", "release": "1.5.0", "replicas": 3, "deployed_by": "runner",
        "reason": "scaled to 3 replicas",
    }  # fmt: skip
    assert ledger_module.active(ledger, "api") == ledger_module.Active("1.5.0", 3)
    assert outcome.structural is not None
    assert len(outcome.structural["containers"]) == 3


def test_scale_down_stops_the_highest_slots_first(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    set_api_replicas(ledger, docker, 3)
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": 1})
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.captured) == ("succeeded", {"previous_replicas": 3})
    assert docker.actions() == [
        ("inspect", "cs-api-150-1"),
        ("inspect", "cs-api-150-3"), ("stop", "cs-api-150-3"),
        ("inspect", "cs-api-150-2"), ("stop", "cs-api-150-2"),
    ]  # fmt: skip
    assert ledger_module.active(ledger, "api") == ledger_module.Active("1.5.0", 1)


def test_scale_up_that_never_gets_healthy_is_undone_and_stops_nothing(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    docker.on_start["cs-api-150-3"] = ("exited", "none")
    before = ledger.read_bytes()
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": 3})
    outcome, _ = run(executor, request)
    assert outcome.status == "failed"
    assert docker.writes() == [
        ("start", "cs-api-150-2"), ("start", "cs-api-150-3"),
        ("stop", "cs-api-150-2"), ("stop", "cs-api-150-3"),
    ]  # fmt: skip
    assert docker.containers["cs-api-150-1"].state == "running"
    assert ledger.read_bytes() == before


def test_scale_down_never_stops_when_the_kept_slots_are_unhealthy(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    set_api_replicas(ledger, docker, 2)
    docker.add("cs-api-150-1", "api", "1.5.0", 1, "running", "unhealthy")
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": 1})
    outcome, _ = run(executor, request)
    assert outcome.status == "failed" and docker.writes() == []  # cs-api-150-2 keeps serving


def test_a_start_failure_while_scaling_up_undoes_the_started_slots(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    docker.fail.add(("start", "cs-api-150-3"))
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": 3})
    outcome, _ = run(executor, request)
    assert outcome.status == "failed"
    assert docker.writes() == [
        ("start", "cs-api-150-2"), ("start", "cs-api-150-3"), ("stop", "cs-api-150-2"),
    ]  # fmt: skip


def test_scale_to_the_current_count_writes_nothing_to_docker_but_records_it(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": 1})
    outcome, _ = run(executor, request)
    assert outcome.status == "succeeded" and docker.writes() == []
    assert ledger_lines(ledger)[-1]["kind"] == "scale"


def test_scale_after_drift_changes_nothing(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path
) -> None:
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": 3})
    set_api_replicas(ledger, docker, 2)
    before = ledger.read_bytes()
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "STATE_DRIFT")
    assert docker.writes() == [] and ledger.read_bytes() == before


@pytest.mark.parametrize("replicas", [0, 6, -1])
def test_scale_outside_the_catalogue_bounds_never_runs_in_the_executor(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, replicas: int
) -> None:
    """checks.py refuses it first (below); the executor refuses it again."""
    request = approved_for(dry_runner, "scale_service", {"service": "api", "replicas": replicas})
    outcome, _ = run(executor, request)
    assert (outcome.status, outcome.error_code) == ("aborted", "VALIDATION_ERROR")
    assert docker.writes() == []


@pytest.mark.parametrize("replicas", [0, 6, "3", 2.5, True])
def test_scale_outside_the_catalogue_bounds_is_refused_by_the_checks(
    checker: Checker, keys: Keys, replicas: Any
) -> None:
    message = signed_for(
        build_request("execute", "scale_service", {"service": "api", "replicas": replicas},
                      now=NOW), keys,
    )  # fmt: skip
    decision = checker.check(entry(message))
    assert isinstance(decision, Refused) and decision.error_code == "VALIDATION_ERROR"


def test_scale_of_a_service_the_catalogue_does_not_allow_is_refused(
    checker: Checker, keys: Keys
) -> None:
    message = signed_for(
        build_request("execute", "scale_service", {"service": "worker", "replicas": 2},
                      now=NOW), keys,
    )  # fmt: skip
    decision = checker.check(entry(message))
    assert isinstance(decision, Refused) and decision.error_code == "VALIDATION_ERROR"


# --- clear_cache (RUN-012) -------------------------------------------------------------


def test_clear_cache_clears_reports_the_keys_and_checks_api_and_redis(
    executor: Executor, dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path,
    cache: FakeCache,
) -> None:  # fmt: skip
    before = ledger.read_bytes()
    outcome, steps = run(executor, approved_for(dry_runner, "clear_cache", {"cache": "pricing"}))
    assert (outcome.status, outcome.captured) == ("succeeded", {})
    assert cache.calls == ["pricing"]
    assert ("clearing the pricing cache", "succeeded", "pricing: 12 keys cleared") in steps
    assert docker.writes() == [] and ledger.read_bytes() == before
    assert outcome.structural is not None
    assert [c["name"] for c in outcome.structural["containers"]] == ["cs-api-150-1", "cs-redis"]
    assert [p["url"] for p in outcome.structural["probes"]] == [
        "http://cs-api-150-1:8000/healthz", LB_PROBE,
    ]  # fmt: skip


def test_clear_cache_of_an_empty_cache_succeeds_with_zero(
    executor: Executor, dry_runner: DryRunner, cache: FakeCache
) -> None:
    cache.answer = False
    outcome, steps = run(executor, approved_for(dry_runner, "clear_cache", {"cache": "catalog"}))
    assert outcome.status == "succeeded"
    assert ("clearing the catalog cache", "succeeded",
            "catalog: already empty, 0 keys cleared") in steps  # fmt: skip


def test_clear_cache_without_stats_still_clears(
    executor: Executor, dry_runner: DryRunner, cache: FakeCache
) -> None:
    request = approved_for(dry_runner, "clear_cache", {"cache": "catalog"})

    def down() -> dict[str, Any]:
        raise CacheStatsUnavailable("HTTP 503")

    dry_runner.cache_stats = down
    outcome, steps = run(executor, request)
    assert outcome.status == "succeeded" and cache.calls == ["catalog"]
    assert ("clearing the catalog cache", "succeeded",
            "catalog: cleared (key count unavailable)") in steps  # fmt: skip


def test_a_failed_clear_fails_without_a_structural_check(
    executor: Executor, dry_runner: DryRunner, cache: FakeCache
) -> None:
    cache.error = CacheClearFailed("HTTP 401")
    outcome, steps = run(executor, approved_for(dry_runner, "clear_cache", {"cache": "pricing"}))
    assert outcome.status == "failed" and outcome.structural is None
    assert ("clearing the pricing cache", "failed", "HTTP 401") in steps


def test_clear_cache_without_a_cache_client_is_refused(
    executor: Executor, dry_runner: DryRunner, cache: FakeCache
) -> None:
    executor.cache_clear = None
    outcome, _ = run(executor, approved_for(dry_runner, "clear_cache", {"cache": "pricing"}))
    assert (outcome.status, outcome.error_code) == ("aborted", "VALIDATION_ERROR")


# --- A2.4b through the consumer: replays and refusals ---------------------------------


def signed_action(dry_runner: DryRunner, keys: Keys, action: str, params: Any) -> dict[str, Any]:
    request = approved_for(dry_runner, action, params)
    message = build_request("execute", action, params, now=NOW,
                            state_fingerprint=request.state_fingerprint)  # fmt: skip
    return signed_for(message, keys)


@pytest.mark.parametrize(
    ("action", "params"),
    [("restart_service", {"service": "api"}),
     ("scale_service", {"service": "api", "replicas": 2}),
     ("clear_cache", {"cache": "pricing"})],
)  # fmt: skip
def test_a_replayed_execute_of_each_action_runs_once(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, docker: FakeDockerClient,
    cache: FakeCache, keys: Keys, action: str, params: Any,
) -> None:  # fmt: skip
    message = signed_action(dry_runner, keys, action, params)
    streams.add("ocp:runner:requests", entry(message))
    streams.add("ocp:runner:requests", entry(message))
    entries = streams.read_group("runner", "runner-a", "ocp:runner:requests", ">", 10, None)
    decisions = [consumer.handle(i, f) for i, f in entries]
    assert [type(d) for d in decisions] == [Executed, Duplicate]
    final = [r for r in results(streams) if r["type"] == "execution_result"]
    assert len(final) == 1 and final[0]["status"] == "succeeded"
    assert verify(final[0], keys.link)
    assert len(docker.writes()) + len(cache.calls) == 1  # one write: restart, start or clear


@pytest.mark.parametrize(
    ("action", "params", "forged"),
    [
        ("restart_service", {"service": "api"}, True),
        ("scale_service", {"service": "api", "replicas": 2}, True),
        ("clear_cache", {"cache": "pricing"}, True),
        ("clear_cache", {"cache": "sessions"}, False),  # outside the catalogue (RUN-012)
        ("restart_service", {"service": "backend"}, False),  # outside the targets
    ],
)
def test_refused_executes_of_each_action_change_nothing(
    consumer: Consumer, streams: FakeStreams, docker: FakeDockerClient, cache: FakeCache,
    keys: Keys, action: str, params: Any, forged: bool,
) -> None:  # fmt: skip
    message = build_request("execute", action, params, now=NOW)
    sent = {**message, "sig": "7" * 64} if forged else signed_for(message, keys)
    streams.add("ocp:runner:requests", entry(sent))
    consumer.poll()
    assert docker.writes() == [] and cache.calls == []
    (final,) = results(streams)
    expected = "BAD_SIGNATURE" if forged else ("VALIDATION_ERROR", "TARGET_NOT_ALLOWED")
    assert final["status"] == "refused" and final["error_code"] in expected
