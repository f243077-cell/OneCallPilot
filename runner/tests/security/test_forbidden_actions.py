"""TEST-004: forbidden actions are refused with zero Docker write calls.

Each forbidden request goes through the whole runner as the backend would send
it: onto ocp:runner:requests, through the consumer, the checks, the dry runner
and the executor, with the fake Docker recording every call (the proxy request
log of TEST-004's "Accept"). Every case is sent signed with the right key and
unsigned (no signature, a wrong signature, or the other key), as an execute
and, where it applies, as a dry run.

The cases: an unknown action, a disabled action, a non-allowlisted container,
a free-form parameter, an expired request, a replayed request and a
rate-limited request. Each must end with a signed refusal (a replay with no
second answer), nothing written to Docker, the cache endpoint untouched, the
ledger unchanged and the stream entry acknowledged.

A safety test (CLAUDE.md §6.11): never skip or loosen it.
"""
# ruff: noqa: F811  (each test redefines an imported fixture name as its argument)

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from oncallpilot_runner.consumer import REQUESTS_GROUP, REQUESTS_STREAM, RESULTS_STREAM, Consumer
from oncallpilot_runner.dry_run import DryRunner
from oncallpilot_runner.limits import COOLDOWN_SECONDS, cooldown_key, rate_key
from oncallpilot_runner.signing import Keys, verify
from oncallpilot_runner.targets import Targets
from tests.conftest import NOW, TARGETS
from tests.fakes import FakeDockerClient, FakeStreams
from tests.publish_signed import build_request, entry, signed_for
from tests.test_execute import (  # noqa: F401 (fixtures)
    FakeCache,
    approved_for,
    cache,
    consumer,
    executor,
    failing_probes,
    ledger,
    ticks,
)

Params = dict[str, str | int]

# (case, action, params, expected refusal) for a request that is forbidden by
# its content alone. "Non-allowlisted container": backend-api carries an api
# slot's labels in the fake testbed; postgres is a Chaos Shop container that is
# not a target; neither is in the catalogue's enum.
FORBIDDEN: list[tuple[str, str, Params, str]] = [
    ("unknown action", "exec_shell", {"cmd": "id"}, "ACTION_NOT_ALLOWED"),
    ("unknown action", "delete_container", {"service": "api"}, "ACTION_NOT_ALLOWED"),
    ("disabled action", "run_migration_rollback", {"migration_id": "0007"},
     "ACTION_NOT_ALLOWED"),
    ("non-allowlisted container", "restart_service", {"service": "backend-api"},
     "VALIDATION_ERROR"),
    ("non-allowlisted container", "restart_service", {"service": "cs-postgres"},
     "VALIDATION_ERROR"),
    ("non-allowlisted container", "rollback_deploy",
     {"service": "socket-proxy-rw", "target_release": "1.4.0"}, "VALIDATION_ERROR"),
    ("free-form parameter", "restart_service", {"service": "api; rm -rf /"}, "VALIDATION_ERROR"),
    ("free-form parameter", "rollback_deploy",
     {"service": "api", "target_release": "1.4.0 && docker run alpine"}, "VALIDATION_ERROR"),
    ("free-form parameter", "scale_service", {"service": "api", "replicas": "2; reboot"},
     "VALIDATION_ERROR"),
    ("free-form parameter", "scale_service", {"service": "api", "replicas": 50},
     "VALIDATION_ERROR"),
    ("free-form parameter", "clear_cache", {"cache": "*"}, "VALIDATION_ERROR"),
    ("free-form parameter", "restart_service", {"service": "api", "command": "id"},
     "VALIDATION_ERROR"),
]  # fmt: skip
IDS = [f"{case}: {action} {json.dumps(params)}" for case, action, params, _ in FORBIDDEN]


def unsigned(message: dict[str, Any], keys: Keys) -> list[dict[str, Any]]:
    """The ways a request can arrive without a valid signature."""
    no_sig = {k: v for k, v in message.items() if k != "sig"}
    return [
        no_sig,
        {**no_sig, "sig": "0" * 64},
        {**no_sig, "sig": ""},
        signed_for(no_sig, keys, wrong_key=True),
    ]


class Runner:
    """The runner under test, with everything TEST-004 asserts on."""

    def __init__(
        self, consumer: Consumer, streams: FakeStreams, docker: FakeDockerClient,
        cache: FakeCache, ledger: Path, keys: Keys,
    ) -> None:  # fmt: skip
        self.consumer, self.streams, self.docker = consumer, streams, docker
        self.cache, self.ledger, self.keys = cache, ledger, keys
        self.ledger_before = ledger.read_bytes()
        docker.calls.clear()

    def send(self, message: dict[str, Any]) -> None:
        self.streams.add(REQUESTS_STREAM, entry(message))
        self.consumer.poll()

    def results(self) -> list[dict[str, Any]]:
        return [json.loads(f["msg"]) for _, f in self.streams.streams.get(RESULTS_STREAM, [])]

    def assert_nothing_changed(self) -> None:
        assert self.docker.writes() == []
        assert self.cache.calls == []
        assert self.ledger.read_bytes() == self.ledger_before
        assert self.streams.pending[(REQUESTS_STREAM, REQUESTS_GROUP)] == {}

    def assert_refused(self, message: dict[str, Any], code: str) -> None:
        """The last result is a refusal of `message`, signed with RUNNER_LINK_KEY only."""
        result = self.results()[-1]
        assert (result["status"], result["error_code"]) == ("refused", code), result
        assert result["request_id"] == message["request_id"]
        assert result["execution_id"] == message.get("execution_id")
        expected = "execution_result" if message["type"] == "execute" else "dry_run_result"
        assert result["type"] == expected
        assert verify(result, self.keys.link) and not verify(result, self.keys.action)
        if message["type"] == "execute":
            assert result["health_after"] is None and result["captured"] is None


@pytest.fixture
def runner(
    consumer: Consumer, streams: FakeStreams, docker: FakeDockerClient, cache: FakeCache,
    ledger: Path, keys: Keys,
) -> Runner:  # fmt: skip
    return Runner(consumer, streams, docker, cache, ledger, keys)


def executes_and_dry_runs(action: str, params: Params) -> list[dict[str, Any]]:
    return [build_request(kind, action, params, now=NOW) for kind in ("execute", "dry_run")]


# --- forbidden by content: unknown, disabled, non-allowlisted, free-form ----------------


@pytest.mark.parametrize(("case", "action", "params", "code"), FORBIDDEN, ids=IDS)
def test_a_signed_forbidden_request_is_refused_and_writes_nothing(
    runner: Runner, keys: Keys, case: str, action: str, params: Params, code: str
) -> None:
    for message in executes_and_dry_runs(action, params):
        signed = signed_for(message, keys)
        runner.send(signed)
        runner.assert_refused(signed, code)
    runner.assert_nothing_changed()


@pytest.mark.parametrize(("case", "action", "params", "code"), FORBIDDEN, ids=IDS)
def test_an_unsigned_forbidden_request_is_refused_and_writes_nothing(
    runner: Runner, keys: Keys, case: str, action: str, params: Params, code: str
) -> None:
    for message in executes_and_dry_runs(action, params):
        for forged in unsigned(signed_for(message, keys), keys):
            forged = {**forged, "request_id": build_request(now=NOW)["request_id"]}
            if forged["type"] == "execute":  # a fresh ID: each forgery gets its own answer
                forged["execution_id"] = build_request(now=NOW)["execution_id"]
            runner.send(forged)
            runner.assert_refused(forged, "BAD_SIGNATURE")
    runner.assert_nothing_changed()


def test_a_service_in_the_catalogue_but_not_in_the_target_allowlist_is_refused(
    runner: Runner, consumer: Consumer, keys: Keys, tmp_path: Path
) -> None:
    narrow = tmp_path / "targets.yaml"
    narrow.write_text(TARGETS.read_text(encoding="utf-8").split("  redis:")[0], encoding="utf-8")
    consumer.checker.targets = Targets.load(narrow)  # the catalogue still lists redis
    for message in executes_and_dry_runs("restart_service", {"service": "redis"}):
        signed = signed_for(message, keys)
        runner.send(signed)
        runner.assert_refused(signed, "TARGET_NOT_ALLOWED")
        for forged in unsigned(signed, keys)[:1]:
            forged = {**forged, "request_id": build_request(now=NOW)["request_id"]}
            if forged["type"] == "execute":
                forged["execution_id"] = build_request(now=NOW)["execution_id"]
            runner.send(forged)
            runner.assert_refused(forged, "BAD_SIGNATURE")
    runner.assert_nothing_changed()


# --- forbidden by time: expired ---------------------------------------------------------


ACTIONS: list[tuple[str, Params]] = [
    ("rollback_deploy", {"service": "api", "target_release": "1.4.0"}),
    ("restart_service", {"service": "api"}),
    ("scale_service", {"service": "api", "replicas": 2}),
    ("clear_cache", {"cache": "pricing"}),
]
ACTION_IDS = [action for action, _ in ACTIONS]


def genuine_execute(
    dry_runner: DryRunner, keys: Keys, action: str, params: Params, now: datetime = NOW
) -> dict[str, Any]:
    """An execute the backend could really send: approved, with its dry run's fingerprint."""
    fingerprint = approved_for(dry_runner, action, params).state_fingerprint
    message = build_request("execute", action, params, now=now, state_fingerprint=fingerprint)
    return signed_for(message, keys)


@pytest.mark.parametrize(("action", "params"), ACTIONS, ids=ACTION_IDS)
def test_an_expired_request_is_refused_and_writes_nothing(
    runner: Runner, dry_runner: DryRunner, keys: Keys, action: str, params: Params
) -> None:
    old = NOW - timedelta(seconds=61)
    expired = genuine_execute(dry_runner, keys, action, params, now=old)
    dry = signed_for(build_request("dry_run", action, params, now=old), keys)
    runner.docker.calls.clear()
    for message in (expired, dry):
        runner.send(message)
        runner.assert_refused(message, "REQUEST_EXPIRED")
    late = {**expired, "execution_id": build_request(now=NOW)["execution_id"],
            "expires_at": "2099-01-01T00:00:00Z"}  # fmt: skip
    runner.send(late)  # pushing expires_at forward breaks the signature
    runner.assert_refused(late, "BAD_SIGNATURE")
    runner.assert_nothing_changed()


# --- forbidden by history: replayed -----------------------------------------------------


@pytest.mark.parametrize(("action", "params"), ACTIONS, ids=ACTION_IDS)
def test_a_replayed_execute_adds_no_write_and_no_answer(
    runner: Runner, dry_runner: DryRunner, keys: Keys, action: str, params: Params
) -> None:
    genuine = genuine_execute(dry_runner, keys, action, params)
    runner.docker.calls.clear()
    runner.send(genuine)
    first = runner.results()
    assert first[-1]["status"] == "succeeded", first[-1]
    writes, cache_calls = list(runner.docker.writes()), list(runner.cache.calls)
    assert writes or cache_calls  # the genuine execute did change something
    for replay in (genuine, *unsigned(genuine, keys)):
        runner.send(replay)  # signed or not, the execution_id is already claimed
    assert runner.results() == first  # no second answer
    assert runner.docker.writes() == writes and runner.cache.calls == cache_calls
    assert runner.streams.pending[(REQUESTS_STREAM, REQUESTS_GROUP)] == {}


def test_a_replay_under_a_new_execution_id_is_a_forgery(
    runner: Runner, dry_runner: DryRunner, keys: Keys
) -> None:
    genuine = genuine_execute(dry_runner, keys, "restart_service", {"service": "api"})
    copied = {**genuine, "execution_id": build_request(now=NOW)["execution_id"]}
    runner.docker.calls.clear()
    runner.send(copied)
    runner.assert_refused(copied, "BAD_SIGNATURE")
    runner.assert_nothing_changed()


# --- forbidden by the second gate: rate-limited (and cooling down) ----------------------


def close_rate_limit(streams: FakeStreams, service: str) -> None:
    for n in range(3):
        streams.zadd(rate_key(service), f"earlier-{n}", NOW.timestamp() - 60)


def close_cooldown(streams: FakeStreams, service: str) -> None:
    streams.set_ex(cooldown_key(service), COOLDOWN_SECONDS)


@pytest.mark.parametrize(
    ("close", "code"), [(close_rate_limit, "RATE_LIMITED"), (close_cooldown, "COOLDOWN_ACTIVE")]
)
@pytest.mark.parametrize(("action", "params"), ACTIONS, ids=ACTION_IDS)
def test_a_rate_limited_execute_is_refused_and_writes_nothing(
    runner: Runner, dry_runner: DryRunner, streams: FakeStreams, keys: Keys,
    action: str, params: Params, close: Callable[[FakeStreams, str], None], code: str,
) -> None:  # fmt: skip
    genuine = genuine_execute(dry_runner, keys, action, params)
    close(streams, "api")  # clear_cache counts under api too
    runner.docker.calls.clear()
    runner.send(genuine)
    runner.assert_refused(genuine, code)
    forged = {**unsigned(genuine, keys)[1], "execution_id": build_request(now=NOW)["execution_id"]}
    runner.send(forged)
    runner.assert_refused(forged, "BAD_SIGNATURE")
    runner.assert_nothing_changed()
