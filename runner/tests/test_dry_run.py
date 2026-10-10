"""Dry runs (RUN-007, architecture §7.5): what each action would do, the state
fingerprint, and proof that nothing changed.

Every dry run goes through `run`, which checks that it changed nothing: the
fake Docker recorded no create, start, stop, restart, kill or remove call,
every container is exactly as before, and the ledger is byte for byte the same.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from oncallpilot_runner.checks import Accepted, Checker
from oncallpilot_runner.consumer import RESULTS_STREAM, Consumer, Previewed
from oncallpilot_runner.dry_run import DryRunner, DryRunUnavailable
from oncallpilot_runner.fingerprint import state_fingerprint
from oncallpilot_runner.request import Request, parse
from oncallpilot_runner.shop_admin import CacheStatsUnavailable
from oncallpilot_runner.signing import Keys, verify
from tests.conftest import NOW
from tests.fakes import FakeDockerClient, FakeStreams
from tests.publish_signed import build_request, entry, signed_for

Run = Callable[[str, dict[str, str | int]], dict[str, Any]]


@pytest.fixture
def run(dry_runner: DryRunner, docker: FakeDockerClient, ledger: Path) -> Run:
    """A dry run, checked to have changed nothing: no write call to Docker,
    every container as it was just before, the ledger byte for byte the same."""

    def go(action: str, params: dict[str, str | int]) -> dict[str, Any]:
        before, ledger_before = docker.snapshot(), ledger.read_bytes()
        try:
            return dry_runner.run(dry(action, params))
        finally:
            assert docker.writes() == []
            assert docker.snapshot() == before
            assert ledger.read_bytes() == ledger_before

    return go


def dry(action: str, params: dict[str, str | int]) -> Request:
    """A parsed dry_run request (the signature is checked earlier, in checks.py)."""
    return parse({**build_request("dry_run", action, params, now=NOW), "sig": "0" * 64})


def changes(preview: dict[str, Any]) -> list[tuple[str, str]]:
    return [(c["target"], c["change"]) for c in preview["would_change"]]


def write_ledger(ledger: Path, *records: dict[str, Any]) -> None:
    ledger.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


# --- rollback_deploy (first: the thin slice depends on it) ----------------------


def test_rollback_starts_the_target_release_then_stops_the_active_one(run: Run) -> None:
    preview = run("rollback_deploy", {"service": "api", "target_release": "1.4.0"})
    assert changes(preview) == [("cs-api-140-1", "start"), ("cs-api-150-1", "stop")]
    assert preview["current"] == {
        "active_release": "1.5.0",
        "running_replicas": 1,
        "containers": [
            {"name": "cs-api-140-1", "state": "created", "health": "none"},
            {"name": "cs-api-150-1", "state": "running", "health": "healthy"},
        ],
        "cache_keys": None,
    }
    assert preview["preconditions"] == {
        "rate_limit_ok": True,
        "cooldown_ok": True,
        "slots_available": True,
    }
    assert preview["warnings"] == []
    assert "1.5.0 -> 1.4.0" in preview["predicted_effect"]
    assert preview["state_fingerprint"] == state_fingerprint(
        "1.5.0", 1, ["cs-api-150-1", "cs-api-140-1"]
    )


def test_rollback_uses_the_ledger_replica_count_for_n(
    run: Run, docker: FakeDockerClient, ledger: Path
) -> None:
    write_ledger(
        ledger,
        {"service": "api", "release": "1.4.0", "replicas": 1},
        {"service": "api", "release": "1.5.0", "replicas": 3},
    )
    for replica in (1, 2):  # slot 3 crashed: N stays 3, with a warning
        docker.add(f"cs-api-150-{replica}", "api", "1.5.0", replica, state="running")
    docker.add("cs-api-150-3", "api", "1.5.0", 3, state="exited")
    preview = run("rollback_deploy", {"service": "api", "target_release": "1.4.0"})
    assert changes(preview) == [
        ("cs-api-140-1", "start"), ("cs-api-140-2", "start"), ("cs-api-140-3", "start"),
        ("cs-api-150-1", "stop"), ("cs-api-150-2", "stop"),
    ]  # fmt: skip
    assert preview["current"]["running_replicas"] == 2
    assert preview["warnings"] == ["2 of 3 replicas of 1.5.0 are running"]
    names = [f"cs-api-{t}-{r}" for t in ("140", "150") for r in (1, 2, 3)]
    assert preview["state_fingerprint"] == state_fingerprint("1.5.0", 3, names)


def test_rollback_reports_missing_target_slots(run: Run, docker: FakeDockerClient) -> None:
    del docker.containers["cs-api-140-1"]
    preview = run("rollback_deploy", {"service": "api", "target_release": "1.4.0"})
    assert preview["preconditions"]["slots_available"] is False
    assert "1.4.0 has 0 of 1 slot containers" in preview["warnings"]


def test_rollback_warns_when_a_target_slot_already_runs(run: Run, docker: FakeDockerClient) -> None:
    docker.add("cs-api-140-1", "api", "1.4.0", 1, state="running", health="healthy")
    preview = run("rollback_deploy", {"service": "api", "target_release": "1.4.0"})
    assert changes(preview) == [("cs-api-150-1", "stop")]
    assert "cs-api-140-1 is already running" in preview["warnings"]


def test_rollback_of_the_worker(run: Run, ledger: Path) -> None:
    write_ledger(
        ledger,
        {"service": "worker", "release": "2.2.0", "replicas": 1},
        {"service": "worker", "release": "2.1.0", "replicas": 1},
    )
    preview = run("rollback_deploy", {"service": "worker", "target_release": "2.2.0"})
    assert changes(preview) == [("cs-worker-220", "start"), ("cs-worker-210", "stop")]


def test_containers_outside_the_allowlist_are_never_targets(
    run: Run, docker: FakeDockerClient
) -> None:
    """backend-api carries the api labels but fails the name regex (§7.4)."""
    preview = run("rollback_deploy", {"service": "api", "target_release": "1.4.0"})
    shown = [c["name"] for c in preview["current"]["containers"]]
    assert "backend-api" not in shown and all(t != "backend-api" for t, _ in changes(preview))
    assert preview["current"]["running_replicas"] == 1  # backend-api is not counted


def test_a_service_without_ledger_records_cannot_be_rolled_back(run: Run, ledger: Path) -> None:
    write_ledger(ledger, {"service": "worker", "release": "2.1.0", "replicas": 1})
    with pytest.raises(DryRunUnavailable):
        run("rollback_deploy", {"service": "api", "target_release": "1.4.0"})


def test_docker_unavailable_or_an_unreadable_ledger_fails_closed(
    run: Run, docker: FakeDockerClient, ledger: Path
) -> None:
    docker.unavailable = True
    with pytest.raises(DryRunUnavailable, match="Docker did not answer"):
        run("rollback_deploy", {"service": "api", "target_release": "1.4.0"})
    docker.unavailable = False
    ledger_bytes = ledger.read_bytes()
    ledger.write_text("not json\n", encoding="utf-8")
    try:
        with pytest.raises(DryRunUnavailable, match="not JSON"):
            run("restart_service", {"service": "api"})
    finally:
        ledger.write_bytes(ledger_bytes)


def test_a_record_without_replicas_is_unreadable(run: Run, ledger: Path) -> None:
    ledger_bytes = ledger.read_bytes()
    write_ledger(ledger, {"service": "api", "release": "1.5.0"})
    try:
        with pytest.raises(DryRunUnavailable, match="no valid release and replicas"):
            run("scale_service", {"service": "api", "replicas": 2})
    finally:
        ledger.write_bytes(ledger_bytes)


# --- through the consumer: a signed, verified dry_run_result --------------------


@pytest.fixture
def consumer(streams: FakeStreams, checker: Checker, dry_runner: DryRunner) -> Consumer:
    c = Consumer(streams, checker, "runner-a", heartbeat=None, dry_runner=dry_runner)
    c.ensure_group()
    return c


def results(streams: FakeStreams) -> list[dict[str, Any]]:
    return [json.loads(f["msg"]) for _, f in streams.streams.get(RESULTS_STREAM, [])]


def test_a_dry_run_request_gets_a_signed_ok_result(
    consumer: Consumer, streams: FakeStreams, keys: Keys
) -> None:
    message = signed_for(build_request("dry_run", now=NOW), keys)  # rollback api -> 1.4.0
    streams.add("ocp:runner:requests", entry(message))
    assert consumer.poll() == 1
    (result,) = results(streams)
    assert (result["type"], result["status"], result["error_code"]) == (
        "dry_run_result",
        "ok",
        None,
    )
    assert (result["request_id"], result["execution_id"]) == (message["request_id"], None)
    assert verify(result, keys.link) and not verify(result, keys.action)
    assert changes(result["dry_run"]) == [("cs-api-140-1", "start"), ("cs-api-150-1", "stop")]
    assert result["step"] is None and result["health_after"] is None and result["captured"] is None
    assert streams.keys == {}  # a dry run claims no idempotency key


def test_an_unreadable_state_refuses_the_dry_run(
    consumer: Consumer, streams: FakeStreams, docker: FakeDockerClient, keys: Keys
) -> None:
    docker.unavailable = True
    streams.add("ocp:runner:requests", entry(signed_for(build_request("dry_run", now=NOW), keys)))
    consumer.poll()
    (result,) = results(streams)
    assert (result["status"], result["error_code"], result["dry_run"]) == (
        "refused", "VALIDATION_ERROR", None,
    )  # fmt: skip
    assert verify(result, keys.link)


def test_a_refused_dry_run_never_reaches_docker(
    consumer: Consumer, streams: FakeStreams, docker: FakeDockerClient, keys: Keys
) -> None:
    forged = {**build_request("dry_run", now=NOW), "sig": "5" * 64}
    streams.add("ocp:runner:requests", entry(forged))
    consumer.poll()
    assert docker.calls == []


def test_an_execute_runs_no_dry_run(
    consumer: Consumer, streams: FakeStreams, docker: FakeDockerClient, keys: Keys
) -> None:
    streams.add("ocp:runner:requests", entry(signed_for(build_request(now=NOW), keys)))
    (entry_id, fields), *_ = streams.read_group(
        "runner", "runner-a", "ocp:runner:requests", ">", 1, None
    )
    assert isinstance(consumer.handle(entry_id, fields), Accepted)
    assert docker.calls == [] and results(streams) == []


def test_without_a_dry_runner_a_dry_run_is_refused(
    streams: FakeStreams, checker: Checker, keys: Keys
) -> None:
    bare = Consumer(streams, checker, "runner-a", heartbeat=None)
    bare.ensure_group()
    streams.add("ocp:runner:requests", entry(signed_for(build_request("dry_run", now=NOW), keys)))
    bare.poll()
    assert results(streams)[0]["error_code"] == "VALIDATION_ERROR"


def test_previewed_decisions_are_returned(
    consumer: Consumer, streams: FakeStreams, keys: Keys
) -> None:
    streams.add("ocp:runner:requests", entry(signed_for(build_request("dry_run", now=NOW), keys)))
    (entry_id, fields), *_ = streams.read_group(
        "runner", "runner-a", "ocp:runner:requests", ">", 1, None
    )
    decision = consumer.handle(entry_id, fields)
    assert isinstance(decision, Previewed) and decision.result["status"] == "ok"


def test_cache_stats_errors_are_typed() -> None:
    assert issubclass(CacheStatsUnavailable, RuntimeError)


# --- restart_service --------------------------------------------------------------


def test_restart_targets_the_active_release_slots(run: Run) -> None:
    preview = run("restart_service", {"service": "api"})
    assert changes(preview) == [("cs-api-150-1", "restart")]
    assert [c["name"] for c in preview["current"]["containers"]] == ["cs-api-150-1"]
    assert preview["state_fingerprint"] == state_fingerprint("1.5.0", 1, ["cs-api-150-1"])
    assert "SIGTERM, 10 s timeout" in preview["predicted_effect"]


def test_restart_starts_an_exited_container(run: Run, docker: FakeDockerClient) -> None:
    docker.add("cs-worker-210", "worker", "2.1.0", 1, state="exited")
    preview = run("restart_service", {"service": "worker"})
    assert changes(preview) == [("cs-worker-210", "start")]
    assert preview["warnings"] == ["0 of 1 replicas of 2.1.0 are running"]


def test_restart_of_redis_has_no_ledger_values(run: Run) -> None:
    """Ruled 2026-10-10: redis has no ledger records, so release and replicas are null."""
    preview = run("restart_service", {"service": "redis"})
    assert changes(preview) == [("cs-redis", "restart")]
    assert preview["current"]["active_release"] is None
    assert preview["current"]["running_replicas"] is None
    assert preview["state_fingerprint"] == state_fingerprint(None, None, ["cs-redis"])


def test_restart_with_a_missing_container(run: Run, docker: FakeDockerClient) -> None:
    del docker.containers["cs-redis"]
    preview = run("restart_service", {"service": "redis"})
    assert changes(preview) == [] and preview["preconditions"]["slots_available"] is False
    assert "redis is missing target containers" in preview["warnings"]
    assert preview["predicted_effect"] == "redis: nothing to restart"


# --- scale_service ----------------------------------------------------------------


def test_scale_up_starts_slots_of_the_active_release(run: Run) -> None:
    preview = run("scale_service", {"service": "api", "replicas": 3})
    assert changes(preview) == [("cs-api-150-2", "start"), ("cs-api-150-3", "start")]
    names = ["cs-api-150-1", "cs-api-150-2", "cs-api-150-3"]
    assert [c["name"] for c in preview["current"]["containers"]] == names
    assert preview["state_fingerprint"] == state_fingerprint("1.5.0", 1, names)
    assert preview["predicted_effect"].startswith("api 1.5.0: 1 -> 3 running replicas")


def test_scale_down_stops_the_highest_slots(
    run: Run, docker: FakeDockerClient, ledger: Path
) -> None:
    write_ledger(ledger, {"service": "api", "release": "1.5.0", "replicas": 3})
    for replica in (2, 3):
        docker.add(f"cs-api-150-{replica}", "api", "1.5.0", replica, state="running")
    docker.add("cs-api-150-5", "api", "1.5.0", 5, state="running")  # outside 1..N: stray
    preview = run("scale_service", {"service": "api", "replicas": 1})
    assert changes(preview) == [
        ("cs-api-150-2", "stop"), ("cs-api-150-3", "stop"), ("cs-api-150-5", "stop"),
    ]  # fmt: skip
    assert "running outside the desired slots: cs-api-150-5" in preview["warnings"]
    names = ["cs-api-150-1", "cs-api-150-2", "cs-api-150-3"]  # 1..max(N=3, 1)
    assert preview["state_fingerprint"] == state_fingerprint("1.5.0", 3, names)


def test_scale_to_the_current_count_changes_nothing(run: Run) -> None:
    preview = run("scale_service", {"service": "api", "replicas": 1})
    assert changes(preview) == []
    assert "api already runs 1 replicas" in preview["warnings"]


def test_scale_without_enough_slots(run: Run, docker: FakeDockerClient) -> None:
    del docker.containers["cs-api-150-5"]
    preview = run("scale_service", {"service": "api", "replicas": 5})
    assert preview["preconditions"]["slots_available"] is False
    assert "1.5.0 has fewer than 5 slot containers" in preview["warnings"]


# --- clear_cache -------------------------------------------------------------------


def test_clear_cache_reports_the_keys_it_would_clear(run: Run, docker: FakeDockerClient) -> None:
    catalog = run("clear_cache", {"cache": "catalog"})
    pricing = run("clear_cache", {"cache": "pricing"})
    assert changes(catalog) == [("catalog", "clear")] and changes(pricing) == [("pricing", "clear")]
    assert catalog["current"] == {
        "active_release": None, "running_replicas": None, "containers": [], "cache_keys": 1,
    }  # fmt: skip
    assert pricing["current"]["cache_keys"] == 12
    assert "(12 keys)" in pricing["predicted_effect"]
    assert catalog["state_fingerprint"] == pricing["state_fingerprint"]
    assert catalog["state_fingerprint"] == state_fingerprint(None, None, [])
    assert docker.calls == []  # no Docker call at all


@pytest.mark.parametrize(
    "stats",
    [
        {"catalog": {"present": False}, "pricing": {"entries": 0}},
        {"pricing": {"entries": -1}},
        {"pricing": {"entries": True}},
        {"pricing": "x"},
    ],
)
def test_clear_cache_with_odd_stats(dry_runner: DryRunner, stats: dict[str, Any]) -> None:
    dry_runner.cache_stats = lambda: stats
    catalog = dry_runner.run(dry("clear_cache", {"cache": "catalog"}))
    pricing = dry_runner.run(dry("clear_cache", {"cache": "pricing"}))
    if "catalog" in stats:
        assert (catalog["current"]["cache_keys"], pricing["current"]["cache_keys"]) == (0, 0)
    else:
        assert catalog["current"]["cache_keys"] is None
        assert pricing["warnings"] == ["cache stats unavailable: unexpected stats shape"]


def test_clear_cache_when_the_stats_endpoint_is_down(dry_runner: DryRunner) -> None:
    def down() -> dict[str, Any]:
        raise CacheStatsUnavailable("HTTP 503")

    dry_runner.cache_stats = down
    preview = dry_runner.run(dry("clear_cache", {"cache": "catalog"}))
    assert preview["current"]["cache_keys"] is None
    assert preview["warnings"] == ["cache stats unavailable: HTTP 503"]
    assert changes(preview) == [("catalog", "clear")]


# --- preconditions: the runner's own counters (read only) --------------------------


def test_preconditions_read_the_runner_counters(
    run: Run, streams: FakeStreams, clock: list[Any]
) -> None:
    now = clock[0].timestamp()
    streams.zsets["ocp:runner:ratelimit:api"] = {"a": now - 1900, "b": now - 60, "c": now - 30}
    assert run("restart_service", {"service": "api"})["preconditions"]["rate_limit_ok"] is True
    streams.zsets["ocp:runner:ratelimit:api"]["d"] = now - 10  # 3 inside 30 min
    assert run("restart_service", {"service": "api"})["preconditions"]["rate_limit_ok"] is False
    assert run("restart_service", {"service": "worker"})["preconditions"]["rate_limit_ok"] is True
    streams.set_nx("ocp:runner:cooldown:api", 120)
    scaled = run("scale_service", {"service": "api", "replicas": 2})
    assert scaled["preconditions"]["cooldown_ok"] is False


def test_clear_cache_counts_under_api(run: Run, streams: FakeStreams) -> None:
    """Ruled 2026-10-10: the limits of clear_cache are the api's."""
    streams.set_nx("ocp:runner:cooldown:api", 120)
    assert run("clear_cache", {"cache": "catalog"})["preconditions"]["cooldown_ok"] is False


def test_a_dry_run_writes_no_redis_key(run: Run, streams: FakeStreams) -> None:
    calls: list[tuple[str, dict[str, str | int]]] = [
        ("rollback_deploy", {"service": "api", "target_release": "1.4.0"}),
        ("restart_service", {"service": "redis"}),
        ("scale_service", {"service": "api", "replicas": 2}),
        ("clear_cache", {"cache": "pricing"}),
    ]
    for action, params in calls:
        run(action, params)
    assert streams.keys == {} and streams.zsets == {}


def test_an_unknown_action_has_no_dry_run(dry_runner: DryRunner) -> None:
    with pytest.raises(DryRunUnavailable, match="no dry run for exec_shell"):
        dry_runner.run(dry("exec_shell", {}))


def test_a_service_outside_targets_lists_nothing(
    dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    with pytest.raises(DryRunUnavailable, match="backend is not a target"):
        dry_runner.run(dry("restart_service", {"service": "backend"}))
    assert docker.calls == []


def test_the_effect_text_fits_c1(dry_runner: DryRunner, docker: FakeDockerClient) -> None:
    for replica in range(6, 60):  # absurd: many running slots, a long effect text
        docker.add(f"cs-api-150-{replica}", "api", "1.5.0", replica, state="running")
    preview = dry_runner.run(dry("scale_service", {"service": "api", "replicas": 1}))
    assert len(preview["predicted_effect"]) == 300 and preview["predicted_effect"].endswith("...")


def test_a_redis_error_in_a_dry_run_is_retried_not_refused(
    consumer: Consumer, streams: FakeStreams, dry_runner: DryRunner, keys: Keys
) -> None:
    """The counters are unreadable: the entry stays pending for a retry (no ack,
    no result), the same as any Redis outage in the loop."""

    class Down(FakeStreams):
        def zcount(self, key: str, min_score: float, max_score: float) -> int:
            raise RedisConnectionError("down")

    dry_runner.counters = Down()
    streams.add("ocp:runner:requests", entry(signed_for(build_request("dry_run", now=NOW), keys)))
    with pytest.raises(RedisConnectionError):
        consumer.poll()
    assert results(streams) == []
    assert len(streams.pending[("ocp:runner:requests", "runner")]) == 1
