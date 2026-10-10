"""A2.1 end to end: the real runner image against a throwaway Redis (live).

A private Docker network holds a Redis with the `runner` ACL user of
architecture §2.7 (plus a `backend` user that publishes), and the runner image
started the way it will run in compose: uid 10001, read-only root, /tmp tmpfs,
every capability dropped, no Docker socket, 128 MiB. publish_signed.py sends
the requests with the shared vector's TEST-ONLY keys.

    uv run pytest -m live -v        # needs Docker, the built image, and the vector:
    docker build --build-context contracts=contracts -t oncallpilot/runner:0.1.0 runner
    OCP_CONTRACTS_DIR=<checkout with contracts/fixtures/signing/vectors.json>/contracts
"""

import json
import secrets
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis

from oncallpilot_runner.signing import Keys, verify
from tests.publish_signed import build_request, entry, signed, signed_for, vector_keys, vector_path

pytestmark = pytest.mark.live

IMAGE = "oncallpilot/runner:0.1.0"
REDIS_IMAGE = "redis:7.4.11-alpine"
REQUESTS, RESULTS = "ocp:runner:requests", "ocp:runner:results"
# architecture §2.7 / SEC-004, verbatim.
RUNNER_ACL = [
    "~ocp:runner:*", "-@all", "+@stream", "+@sortedset",
    "+set", "+get", "+del", "+expire", "+ttl", "+exists", "+ping",
]  # fmt: skip
LEDGER = (
    '{"service":"api","release":"1.4.0","kind":"deploy"}\\n'
    '{"service":"api","release":"1.5.0","kind":"deploy"}\\n'
)


def docker(*args: str, check: bool = True) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


@pytest.fixture(scope="module")
def keys() -> Keys:
    path = vector_path()
    if path is None:
        pytest.skip("signing vector not found (set OCP_CONTRACTS_DIR)")
    return vector_keys(path)


@pytest.fixture(scope="module")
def stack(keys: Keys) -> Iterator[dict[str, Any]]:
    tag = secrets.token_hex(3)
    net, rds, run, vol = (f"ocp-rt-{tag}", f"ocp-rt-redis-{tag}", f"ocp-rt-runner-{tag}",
                          f"ocp-rt-ledger-{tag}")  # fmt: skip
    runner_pw, backend_pw = secrets.token_hex(16), secrets.token_hex(16)
    try:
        docker("network", "create", net)
        docker(
            "run", "-d", "--name", rds, "--network", net, "--memory", "64m",
            "-p", "127.0.0.1::6379", REDIS_IMAGE,
            "redis-server", "--save", "", "--appendonly", "no",
            "--user", "default", "off",
            "--user", "runner", "on", f">{runner_pw}", *RUNNER_ACL,
            "--user", "backend", "on", f">{backend_pw}", "~*", "+@all",
        )  # fmt: skip
        port = docker("port", rds, "6379/tcp").splitlines()[0].rsplit(":", 1)[1]
        docker("volume", "create", vol)
        docker(
            "run", "--rm", "-v", f"{vol}:/ledger", REDIS_IMAGE, "sh", "-c",
            f"printf '{LEDGER}' > /ledger/deploys.jsonl && chown -R 10001:10001 /ledger",
        )  # fmt: skip
        docker(
            "run", "-d", "--name", run, "--hostname", "runner-1", "--network", net,
            "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "128m",
            "-v", f"{vol}:/ledger",
            "-e", f"RUNNER_REDIS_URL=redis://runner:{runner_pw}@{rds}:6379/0",
            "-e", f"ACTION_SIGNING_KEY={keys.action.hex()}",
            "-e", f"RUNNER_LINK_KEY={keys.link.hex()}",
            "-e", "LEDGER_PATH=/ledger/deploys.jsonl",
            IMAGE,
        )  # fmt: skip
        backend = redis.Redis(
            host="127.0.0.1", port=int(port), username="backend", password=backend_pw,
            decode_responses=True,
        )  # fmt: skip
        as_runner = redis.Redis(
            host="127.0.0.1", port=int(port), username="runner", password=runner_pw,
            decode_responses=True,
        )  # fmt: skip
        deadline = time.monotonic() + 60
        while docker("inspect", "-f", "{{.State.Health.Status}}", run) != "healthy":
            assert time.monotonic() < deadline, docker("logs", run, check=False)
            time.sleep(1)
        yield {"backend": backend, "as_runner": as_runner, "runner": run}
    finally:
        print(docker("logs", run, check=False))
        docker("rm", "-f", run, rds, check=False)
        docker("volume", "rm", vol, check=False)
        docker("network", "rm", net, check=False)


def test_the_runner_answers_every_request_and_fails_closed(
    stack: dict[str, Any], keys: Keys
) -> None:
    backend: redis.Redis = stack["backend"]
    now = datetime.now(UTC)
    good = signed_for(build_request(now=now), keys)
    tampered = signed_for(build_request(now=now), keys)
    tampered["params"]["target_release"] = "1.5.0"
    cases: dict[str, dict[str, Any]] = {
        "BAD_SIGNATURE forged": {**build_request(now=now), "sig": "0" * 64},
        "BAD_SIGNATURE tampered": tampered,
        "BAD_SIGNATURE wrong key": signed_for(build_request(now=now), keys, wrong_key=True),
        "BAD_SIGNATURE unknown key": signed(build_request(now=now), b"x" * 32),
        "REQUEST_EXPIRED": signed_for(build_request(now=now - timedelta(seconds=61)), keys),
        "ACTION_NOT_ALLOWED unknown": signed_for(
            build_request("execute", "exec_shell", {"cmd": "id"}, now=now), keys
        ),
        "ACTION_NOT_ALLOWED disabled": signed_for(
            build_request("execute", "run_migration_rollback", {"migration_id": "7"}, now=now), keys
        ),
        "VALIDATION_ERROR free-form": signed_for(
            build_request("execute", "restart_service", {"service": "api; rm -rf /"}, now=now), keys
        ),
        "VALIDATION_ERROR shape": signed_for({**build_request(now=now), "approved_by": None}, keys),
    }
    backend.xadd(REQUESTS, entry(good))  # type: ignore[arg-type]
    backend.xadd(REQUESTS, entry(good))  # type: ignore[arg-type]  # replay
    # C5 issue 9: a forgery borrowing a real execution_id, then the genuine execute.
    borrowed = signed_for(build_request(now=now), keys)
    cases["BAD_SIGNATURE borrowed id"] = {**borrowed, "sig": "4" * 64}
    backend.xadd(REQUESTS, entry(cases["BAD_SIGNATURE borrowed id"]))  # type: ignore[arg-type]
    backend.xadd(REQUESTS, entry(borrowed))  # type: ignore[arg-type]
    backend.xadd(REQUESTS, {"msg": "not json"})
    for label, message in cases.items():
        if label != "BAD_SIGNATURE borrowed id":  # already published, before `borrowed`
            backend.xadd(REQUESTS, entry(message))  # type: ignore[arg-type]

    deadline = time.monotonic() + 30
    while True:
        answered = backend.xlen(RESULTS)
        pending = backend.xpending(REQUESTS, "runner")["pending"]
        if answered >= len(cases) and pending == 0:
            break
        assert time.monotonic() < deadline, f"{answered} results, {pending} pending"
        time.sleep(0.5)
    time.sleep(2)  # nothing more may arrive (accepted, duplicate and dropped answer nothing)
    rows: Any = backend.xrange(RESULTS)
    results = [json.loads(f["msg"]) for _, f in rows]
    assert len(results) == len(cases)
    by_request = {r["request_id"]: r for r in results}
    for label, message in cases.items():
        result = by_request[message["request_id"]]
        assert result["status"] == "refused", label
        assert result["error_code"] == label.split()[0], label
        assert result["execution_id"] == message["execution_id"], label
        assert verify(result, keys.link) and not verify(result, keys.action), label
    assert good["request_id"] not in by_request

    logs = [json.loads(line) for line in docker("logs", stack["runner"]).splitlines() if line]
    decisions = [line.get("decision") for line in logs]
    assert decisions.count("accepted") == 1  # only `good`; `borrowed` must never run
    assert decisions.count("duplicate") == 2  # the replay of `good`, and `borrowed`
    assert decisions.count("dropped") == 1
    assert decisions.count("refused") == len(cases)
    assert not any(keys.action.hex() in json.dumps(line) for line in logs)  # keys never logged


def test_the_runner_redis_user_is_confined(stack: dict[str, Any]) -> None:
    """SEC-004 (the parts the runner relies on): only ocp:runner:* and its commands."""
    as_runner: redis.Redis = stack["as_runner"]
    assert as_runner.ping()
    assert as_runner.set("ocp:runner:probe", "1", nx=True, ex=5) in (True, None)
    for denied in (
        lambda: as_runner.xadd("ocp:events", {"x": "1"}),
        lambda: as_runner.get("ocp:challenge:abc"),
        lambda: as_runner.keys("*"),
        lambda: as_runner.config_get("*"),
        lambda: as_runner.flushall(),
        lambda: as_runner.eval("return 1", 0),
    ):
        with pytest.raises(redis.exceptions.NoPermissionError):
            denied()


def test_the_container_is_isolated_and_small(stack: dict[str, Any]) -> None:
    info = json.loads(docker("inspect", stack["runner"]))[0]
    host = info["HostConfig"]
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]
    assert "no-new-privileges" in host["SecurityOpt"]
    assert host["Memory"] == 128 * 1024 * 1024
    assert info["Config"]["User"] == "runner"
    assert not any("docker.sock" in m.get("Source", "") for m in info["Mounts"])
    assert not host.get("PortBindings")
    used = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", stack["runner"])
    print(f"runner memory: {used}")
    assert used.split("/")[0].strip().endswith(("MiB", "KiB"))
