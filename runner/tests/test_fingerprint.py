"""The state fingerprint (architecture §7.5, C8 §2; RUN-007, RUN-008).

Relevant: the ledger's active release, its desired replica count, and which
target containers exist. Irrelevant on purpose: container state and health,
restart counts, containers that are not targets, the order Docker lists them.
"""

from pathlib import Path
from typing import Any

import pytest

from oncallpilot_runner.dry_run import DryRunner
from oncallpilot_runner.fingerprint import state_fingerprint
from oncallpilot_runner.request import Request, parse
from tests.conftest import NOW
from tests.fakes import FakeDockerClient
from tests.publish_signed import build_request

ROLLBACK = ("rollback_deploy", {"service": "api", "target_release": "1.4.0"})
ACTIONS = [
    ROLLBACK,
    ("restart_service", {"service": "api"}),
    ("restart_service", {"service": "redis"}),
    ("scale_service", {"service": "api", "replicas": 3}),
]


def dry(action: str, params: dict[str, Any]) -> Request:
    return parse({**build_request("dry_run", action, params, now=NOW), "sig": "0" * 64})


def fp(dry_runner: DryRunner, action: str = ROLLBACK[0], params: Any = None) -> str:
    preview = dry_runner.run(dry(action, params if params is not None else ROLLBACK[1]))
    value: str = preview["state_fingerprint"]
    return value


def append(ledger: Path, line: str) -> None:
    with ledger.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line + "\n")


def test_the_encoding_is_canonical_json_of_the_three_values() -> None:
    import hashlib

    expected = hashlib.sha256(
        b'{"active_release":"1.5.0","desired_replicas":1,'
        b'"target_containers":["cs-api-140-1","cs-api-150-1"]}'
    ).hexdigest()
    assert state_fingerprint("1.5.0", 1, ["cs-api-150-1", "cs-api-140-1"]) == expected
    assert len(expected) == 64 and expected == expected.lower()


@pytest.mark.parametrize(("action", "params"), ACTIONS)
def test_the_same_state_gives_the_same_fingerprint(
    dry_runner: DryRunner, action: str, params: dict[str, Any]
) -> None:
    assert fp(dry_runner, action, params) == fp(dry_runner, action, params)


def test_a_new_active_release_changes_it(dry_runner: DryRunner, ledger: Path) -> None:
    before = fp(dry_runner, "restart_service", {"service": "api"})
    append(ledger, '{"service":"api","release":"1.4.0","kind":"rollback","replicas":1}')
    assert fp(dry_runner, "restart_service", {"service": "api"}) != before


def test_a_new_desired_replica_count_changes_it(dry_runner: DryRunner, ledger: Path) -> None:
    before = fp(dry_runner)
    append(ledger, '{"service":"api","release":"1.5.0","kind":"scale","replicas":2}')
    assert fp(dry_runner) != before


def test_a_target_container_that_disappears_changes_it(
    dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    before = fp(dry_runner)
    del docker.containers["cs-api-140-1"]
    assert fp(dry_runner) != before


def test_a_target_container_that_loses_its_labels_changes_it(
    dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    """A container must pass the allowlist to count; one that stops passing
    leaves the target set, and so changes the fingerprint."""
    before = fp(dry_runner)
    docker.add("cs-api-150-1", "api", "1.5.0", 1, state="running")
    docker.containers["cs-api-150-1"].labels["oncallpilot.managed"] = "false"
    assert fp(dry_runner) != before


@pytest.mark.parametrize(
    ("state", "health"),
    [("exited", "none"), ("restarting", "starting"), ("running", "unhealthy"), ("dead", "none")],
)
def test_container_state_and_health_do_not_change_it(
    dry_runner: DryRunner, docker: FakeDockerClient, state: str, health: str
) -> None:
    """§7.5: a crash-looping target is not drift."""
    before = fp(dry_runner)
    docker.add("cs-api-150-1", "api", "1.5.0", 1, state=state, health=health)
    docker.add("cs-api-140-1", "api", "1.4.0", 1, state="running", health="healthy")
    assert fp(dry_runner) == before


def test_containers_that_are_not_targets_do_not_change_it(
    dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    before = fp(dry_runner)
    docker.add("cs-api-150-4", "api", "1.5.0", 4, state="running")  # beyond N
    docker.add("cs-worker-220", "worker", "2.2.0", 1, state="running")  # other service
    del docker.containers["cs-redis"]
    docker.add("cs-api-140-9", "api", "1.4.0", 9, state="running")  # not a slot of 1..N
    assert fp(dry_runner) == before


def test_restart_counts_and_listing_order_do_not_change_it(
    dry_runner: DryRunner, docker: FakeDockerClient
) -> None:
    before = fp(dry_runner)
    docker.containers = dict(reversed(list(docker.containers.items())))
    docker.containers["cs-api-150-1"].labels["com.docker.compose.restart_count"] = "7"
    assert fp(dry_runner) == before


def test_unrelated_ledger_records_do_not_change_it(dry_runner: DryRunner, ledger: Path) -> None:
    before = fp(dry_runner)
    append(ledger, '{"service":"worker","release":"2.2.0","kind":"deploy","replicas":1}')
    with ledger.open("a", encoding="utf-8", newline="") as f:  # a write in progress
        f.write('{"service":"api","release":"1.5.0","kind":"deploy","rep')
    assert fp(dry_runner) == before
