"""A2.5 live: the runner's own rate limit and cooldown (RUN-006), and its isolation
(RUN-016), with the real runner image, socket-proxy-rw and the testbed.

The stack (tests/live/stack.py) starts a fresh Redis, so the runner's counters
start empty; the runner reaches it as the `runner` ACL user (architecture §2.7),
so the commands the limits use are checked against the real ACL too.

1. Isolation: `docker inspect` of the running runner against tests/isolation.py
   (the same policy the intended compose service is checked against).
2. Cooldown: restart_service of redis; a second one at once is refused
   `COOLDOWN_ACTIVE` and cs-redis is not restarted; after 120 s it works.
3. Rate limit: three restarts of worker, each after the real cooldown ended;
   the fourth, also after its cooldown, is refused `RATE_LIMITED`.
4. clear_cache counts under api: a restart of api right after it is refused
   `COOLDOWN_ACTIVE`.

This module never clears or edits the runner's counters: it only reads them
(through the test's admin Redis user) to wait for a cooldown to end.

The runner's memory is printed during the run (`pytest -s` shows it).
Every refusal is checked to change nothing: no progress step, the same ledger,
and no request but GET in socket-proxy-rw's debug log (the dry run's reads;
clear_cache's dry run reads no container, so it logs none).
`chaos reset` restores the testbed at the end of the module.
"""

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import redis

from oncallpilot_runner.signing import Keys, verify
from tests.isolation import container_problems
from tests.live.stack import PROXY, ledger_text, runner_stack
from tests.live.test_live_dry_run import REQUEST_LINE
from tests.live.test_live_execute import answers, execute_signed, started_at, wait_for
from tests.live.test_live_runner import docker
from tests.publish_signed import build_request, entry, signed_for, vector_keys, vector_path

pytestmark = pytest.mark.live

REQUESTS = "ocp:runner:requests"


@pytest.fixture(scope="module")
def keys() -> Keys:
    path = vector_path()
    if path is None:
        pytest.skip("signing vector not found (set OCP_CONTRACTS_DIR)")
    return vector_keys(path)


@pytest.fixture(scope="module")
def stack(keys: Keys) -> Iterator[dict[str, Any]]:
    with runner_stack(keys, ledger_writable=True) as live:
        yield live


def memory(stack: dict[str, Any]) -> str:
    usage = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", stack["runner"])
    print(f"\nrunner memory: {usage}")
    return usage


def refused_at_once(
    stack: dict[str, Any], keys: Keys, action: str, params: dict[str, str | int], code: str
) -> None:
    ledger_before = ledger_text()
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    final, steps = execute_signed(stack, keys, action, params, fresh_limits=False)
    assert (final["type"], final["status"], final["error_code"]) == (
        "execution_result", "refused", code,
    )  # fmt: skip
    assert verify(final, keys.link) and not verify(final, keys.action)
    assert final["health_after"] is None and final["captured"] is None
    assert steps == []  # nothing was validated, so nothing ran
    assert ledger_text() == ledger_before  # no ledger record
    methods = {m for m, _ in REQUEST_LINE.findall(docker("logs", "--since", since, PROXY))}
    assert methods <= {"GET"}, methods  # at most the dry run's reads: no Docker write


def test_the_running_runner_is_isolated(stack: dict[str, Any]) -> None:
    container = json.loads(docker("inspect", stack["runner"]))[0]
    image = json.loads(docker("image", "inspect", container["Image"]))[0]
    networks = {stack["network"], "oncallpilot_rw_proxy_net", "oncallpilot_chaos_net"}
    assert container_problems(container, image, networks) == []
    assert "DATABASE_URL" not in json.dumps(container["Config"]["Env"])  # RUN-016's own check
    rows = docker("top", stack["runner"], "-eo", "pid,uid").splitlines()[1:]  # ps needs pid
    uids = [row.split()[1] for row in rows]
    assert uids and set(uids) == {"10001"}  # every process, not only the image's USER
    memory(stack)


def test_a_second_execution_inside_120_s_is_refused_then_works_after_the_cooldown(
    stack: dict[str, Any], keys: Keys
) -> None:
    backend: redis.Redis = stack["backend"]
    restart: dict[str, str | int] = {"service": "redis"}
    first, _ = execute_signed(stack, keys, "restart_service", restart, fresh_limits=False)
    assert first["status"] == "succeeded"
    after_first = started_at("cs-redis")
    assert 0 < int(backend.ttl("ocp:runner:cooldown:redis")) <= 120

    refused_at_once(stack, keys, "restart_service", restart, "COOLDOWN_ACTIVE")
    assert started_at("cs-redis") == after_first  # not restarted again

    wait_cooldown(backend, "redis")
    again, _ = execute_signed(stack, keys, "restart_service", restart, fresh_limits=False)
    assert again["status"] == "succeeded"
    assert started_at("cs-redis") != after_first
    assert backend.zcard("ocp:runner:ratelimit:redis") == 2  # only the two that ran
    memory(stack)


def wait_cooldown(backend: redis.Redis, service: str) -> None:
    """Waits for the real cooldown to end (reads the key, never changes it)."""
    deadline = time.monotonic() + 150
    while backend.exists(f"ocp:runner:cooldown:{service}"):
        assert time.monotonic() < deadline, "the cooldown never ended"
        time.sleep(2)


def test_the_fourth_execution_in_30_minutes_is_refused_rate_limited(
    stack: dict[str, Any], keys: Keys
) -> None:
    backend: redis.Redis = stack["backend"]
    restart: dict[str, str | int] = {"service": "worker"}
    for n in range(3):
        final, _ = execute_signed(stack, keys, "restart_service", restart, fresh_limits=False)
        assert final["status"] == "succeeded", (n, final)
        wait_cooldown(backend, "worker")  # the cooldown is not what refuses the 4th
    assert backend.zcard("ocp:runner:ratelimit:worker") == 3
    memory(stack)

    before = started_at("cs-worker-210")
    refused_at_once(stack, keys, "restart_service", restart, "RATE_LIMITED")
    assert started_at("cs-worker-210") == before  # not restarted
    assert backend.zcard("ocp:runner:ratelimit:worker") == 3  # a refusal uses no budget

    # The dry run shows the closed gate, and a dry run uses no budget either.
    dry = signed_for(build_request("dry_run", "restart_service", restart,
                                   now=datetime.now(UTC)), keys)  # fmt: skip
    backend.xadd(REQUESTS, entry(dry))  # type: ignore[arg-type]
    preview = wait_for(backend, "request_id", dry["request_id"], "dry_run_result", 30)
    assert preview["dry_run"]["preconditions"]["rate_limit_ok"] is False
    assert backend.zcard("ocp:runner:ratelimit:worker") == 3
    assert all(verify(m, keys.link) for m in answers(backend, "request_id", dry["request_id"]))


def test_clear_cache_counts_under_api(stack: dict[str, Any], keys: Keys) -> None:
    backend: redis.Redis = stack["backend"]
    cleared, _ = execute_signed(stack, keys, "clear_cache", {"cache": "catalog"},
                                fresh_limits=False)  # fmt: skip
    assert cleared["status"] == "succeeded"
    assert backend.zcard("ocp:runner:ratelimit:api") == 1
    before = started_at("cs-api-150-1")  # scenario 2: 1.5.0 is active
    refused_at_once(stack, keys, "restart_service", {"service": "api"}, "COOLDOWN_ACTIVE")
    assert started_at("cs-api-150-1") == before
    assert backend.zcard("ocp:runner:ratelimit:api") == 1
