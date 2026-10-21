"""NFR-005 (S3.1), the runner's half of the chaos-of-the-copilot test, live.

The real runner image is killed (SIGKILL) at the two moments NFR-005 names:

1. after the approved execute is sent but before the runner reads it, then
   started again: the request waits in the stream, the restarted runner reads
   it and runs it exactly once (one result, one ledger record);
2. in the middle of the execution (right after it started the target slot):
   the restarted runner re-reads its own pending entry, finds the execution ID
   already claimed (RUN-005) and acknowledges it without running anything
   again and without a result. backend-worker then escalates the incident
   `runner_timeout` after EXECUTION_TIMEOUT_SECONDS (FR-024; tested in
   tests/integration/test_outcomes.py).

The worker's half (a restart mid-investigation ends `agent_interrupted`) is
tests/integration/test_agent_jobs.py.
"""

import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
import redis

from oncallpilot_runner.signing import Keys
from tests.live.stack import chaos, ledger_text, runner_stack
from tests.live.test_live_execute import answers, keys, wait_for  # noqa: F401 (fixture)
from tests.live.test_live_runner import docker
from tests.publish_signed import build_request, entry, signed_for

pytestmark = pytest.mark.live

REQUESTS = "ocp:runner:requests"
ROLLBACK: dict[str, str | int] = {"service": "api", "target_release": "1.4.0"}


@pytest.fixture(scope="module")
def stack(keys: Keys) -> Iterator[dict[str, Any]]:  # noqa: F811
    with runner_stack(keys, ledger_writable=True) as live:
        yield live


def fresh_scenario_2(backend: redis.Redis) -> None:
    """api 1.5.0 active again, and the runner's own limits cleared."""
    chaos("reset")
    chaos("inject", "2")
    backend.delete("ocp:runner:ratelimit:api", "ocp:runner:cooldown:api")


def signed_execute(backend: redis.Redis, keys: Keys) -> dict[str, Any]:  # noqa: F811
    """The dry run's fingerprint, then the execute the backend would send."""
    dry = signed_for(build_request("dry_run", params=ROLLBACK, now=datetime.now(UTC)), keys)
    backend.xadd(REQUESTS, entry(dry))  # type: ignore[arg-type]
    preview = wait_for(backend, "request_id", dry["request_id"], "dry_run_result", 30)
    assert preview["status"] == "ok"
    message = build_request(
        "execute", params=ROLLBACK, now=datetime.now(UTC),
        state_fingerprint=preview["dry_run"]["state_fingerprint"],
    )  # fmt: skip
    return signed_for(message, keys)


def restart(runner: str) -> None:
    docker("start", runner)
    deadline = time.monotonic() + 60
    while docker("inspect", "-f", "{{.State.Health.Status}}", runner) != "healthy":
        assert time.monotonic() < deadline, docker("logs", runner, check=False)
        time.sleep(1)


def decisions(runner: str, execution_id: str) -> list[str]:
    lines = [json.loads(x) for x in docker("logs", runner).splitlines() if x.startswith("{")]
    return [x["decision"] for x in lines if x.get("execution_id") == execution_id
            and "decision" in x]  # fmt: skip


def pending_count(backend: redis.Redis) -> int:
    summary: Any = backend.xpending(REQUESTS, "runner")
    return int(summary["pending"])


def rollback_records() -> int:
    return sum(json.loads(x)["kind"] == "rollback" for x in ledger_text().splitlines())


def test_killed_before_reading_the_execute_runs_it_once_after_restart(
    stack: dict[str, Any], keys: Keys,  # noqa: F811
) -> None:  # fmt: skip
    backend: redis.Redis = stack["backend"]
    fresh_scenario_2(backend)
    message = signed_execute(backend, keys)
    before = rollback_records()

    docker("kill", stack["runner"])
    backend.xadd(REQUESTS, entry(message))  # type: ignore[arg-type]
    time.sleep(5)  # the request waits in the stream; nobody reads it
    assert answers(backend, "execution_id", message["execution_id"]) == []
    restart(stack["runner"])  # well within the execute's 60 s expiry

    final = wait_for(backend, "execution_id", message["execution_id"], "execution_result", 200)
    assert (final["status"], final["error_code"]) == ("succeeded", None)
    time.sleep(3)
    mine = answers(backend, "execution_id", message["execution_id"])
    assert [m["type"] for m in mine].count("execution_result") == 1
    assert rollback_records() == before + 1
    assert decisions(stack["runner"], message["execution_id"]) == ["executed"]
    assert pending_count(backend) == 0


def test_killed_mid_execution_the_restart_does_not_repeat_it(
    stack: dict[str, Any], keys: Keys,  # noqa: F811
) -> None:  # fmt: skip
    backend: redis.Redis = stack["backend"]
    runner = stack["runner"]
    fresh_scenario_2(backend)
    message = signed_execute(backend, keys)
    execution_id = message["execution_id"]
    backend.xadd(REQUESTS, entry(message))  # type: ignore[arg-type]

    # Kill it right after it started the target slot.
    deadline = time.monotonic() + 60
    while not any(
        m["type"] == "progress" and m["step"]["name"] == "starting cs-api-140-1"
        and m["step"]["status"] == "succeeded"
        for m in answers(backend, "execution_id", execution_id)
    ):  # fmt: skip
        assert time.monotonic() < deadline, "the execution never started the slot"
        time.sleep(0.2)
    docker("kill", runner)
    started_at = docker("inspect", "-f", "{{.State.StartedAt}}", "cs-api-140-1")
    steps_before = len(answers(backend, "execution_id", execution_id))

    restart(runner)
    time.sleep(15)  # the restarted runner has read its pending entry by now

    mine = answers(backend, "execution_id", execution_id)
    assert len(mine) == steps_before, "nothing more was done or reported"
    assert [m for m in mine if m["type"] == "execution_result"] == []
    assert docker("inspect", "-f", "{{.State.StartedAt}}", "cs-api-140-1") == started_at
    assert decisions(runner, execution_id) == ["duplicate"]  # the restarted run's view
    assert pending_count(backend) == 0
