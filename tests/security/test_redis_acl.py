"""SEC-004: copilot-redis ACL users, checked on a throwaway container.

The container runs the real ``infrastructure/redis/users.acl`` and
``start-redis.sh`` with the same user, read-only root and tmpfs as
``copilot.yml``, published on 127.0.0.1 only for the test. As ``runner``,
everything outside ``ocp:runner:*`` and the §2.7 command list is NOPERM, while
every runner flow still works (consume, idempotency, rate limit, cooldown,
results).
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import redis
from redis.exceptions import AuthenticationError, NoPermissionError, ResponseError

ROOT = Path(__file__).resolve().parents[2]
REDIS_DIR = ROOT / "infrastructure" / "redis"
IMAGE = os.environ.get("OCP_TEST_REDIS_IMAGE", "redis:7.4.11-alpine")

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="needs Docker")


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120)
    if check and done.returncode != 0:
        raise AssertionError(f"docker {args[0]} failed: {done.stderr.strip()}")
    return done


def run_redis(backend_url: str, runner_url: str) -> str:
    """Start the container detached and return its name."""
    name = f"ocp-acl-test-{secrets.token_hex(3)}"
    docker(
        "run", "-d", "--name", name, "-p", "127.0.0.1::6379",
        "-e", f"REDIS_URL={backend_url}", "-e", f"RUNNER_REDIS_URL={runner_url}",
        "-v", f"{REDIS_DIR / 'users.acl'}:/etc/ocp/users.acl.template:ro",
        "-v", f"{REDIS_DIR / 'start-redis.sh'}:/etc/ocp/start-redis.sh:ro",
        "--user", "999:1000", "--read-only", "--cap-drop", "ALL",
        "--tmpfs", "/run/ocp:uid=999,gid=1000,mode=0700", "--tmpfs", "/data:uid=999,gid=1000",
        "--entrypoint", "/bin/sh", IMAGE, "/etc/ocp/start-redis.sh",
    )  # fmt: skip
    return name


def send(client: redis.Redis, command: tuple[str, ...]) -> object:
    return client.execute_command(*command)  # type: ignore[no-untyped-call]


@dataclass
class Clients:
    backend: redis.Redis
    runner: redis.Redis
    anonymous: redis.Redis
    container: str


@pytest.fixture(scope="module")
def clients() -> Iterator[Clients]:
    backend_pw, runner_pw = secrets.token_hex(16), secrets.token_hex(16)
    name = run_redis(
        f"redis://backend:{backend_pw}@copilot-redis:6379/0",
        f"redis://runner:{runner_pw}@copilot-redis:6379/0",
    )
    try:
        port = int(docker("port", name, "6379/tcp").stdout.splitlines()[0].rsplit(":", 1)[1])

        def connect(user: str | None, password: str | None) -> redis.Redis:
            return redis.Redis(
                host="127.0.0.1", port=port, username=user, password=password,
                decode_responses=True, socket_timeout=5,
            )  # fmt: skip

        backend = connect("backend", backend_pw)
        deadline = time.monotonic() + 30
        while True:
            try:
                backend.ping()
                break
            except (redis.ConnectionError, redis.TimeoutError):
                if time.monotonic() > deadline:
                    raise AssertionError(docker("logs", name, check=False).stderr) from None
                time.sleep(0.3)
        yield Clients(backend, connect("runner", runner_pw), connect(None, None), name)
    finally:
        docker("rm", "-f", name, check=False)


def test_default_user_is_disabled(clients: Clients) -> None:
    with pytest.raises((AuthenticationError, ResponseError)):
        clients.anonymous.ping()


def test_healthcheck_command_works(clients: Clients) -> None:
    done = docker(
        "exec", clients.container, "sh", "-c",
        'REDISCLI_AUTH="$(cat /run/ocp/healthcheck.pw)" redis-cli --user backend ping',
    )  # fmt: skip
    assert done.stdout.strip() == "PONG"


@pytest.mark.parametrize(
    "command",
    [
        ("XADD", "ocp:events", "*", "e", "x"),
        ("GET", "ocp:challenge:abc"),
        ("SET", "ocp:idem:approve:u:k", "x"),
        ("KEYS", "*"),
        ("CONFIG", "GET", "*"),
        ("FLUSHALL",),
        ("FLUSHDB",),
        ("EVAL", "return 1", "0"),
        ("PUBLISH", "ocp:runner:x", "hi"),
        ("HSET", "ocp:runner:h", "f", "v"),
        ("ACL", "LIST"),
        ("SCAN", "0"),
        ("SAVE",),
        ("SHUTDOWN", "NOSAVE"),
    ],
)
def test_runner_is_refused_outside_its_rules(clients: Clients, command: tuple[str, ...]) -> None:
    with pytest.raises(NoPermissionError):
        send(clients.runner, command)


def test_runner_flows_work(clients: Clients) -> None:
    r, b = clients.runner, clients.backend
    requests, results = "ocp:runner:requests", "ocp:runner:results"
    assert r.ping()
    # Consume the request stream as a group and acknowledge.
    r.xgroup_create(requests, "runner", id="$", mkstream=True)
    b.xadd(requests, {"msg": "{}"})
    entries: Any = r.xreadgroup("runner", "runner-1", {requests: ">"}, count=1)
    [[_, [(entry_id, fields)]]] = entries
    assert fields == {"msg": "{}"}
    assert r.xack(requests, "runner", entry_id) == 1
    # Results.
    r.xadd(results, {"msg": "{}"})
    assert b.xlen(results) == 1
    # Idempotency key and cooldown.
    assert r.set("ocp:runner:idem:e1", "claimed", nx=True, ex=86400)
    assert not r.set("ocp:runner:idem:e1", "claimed", nx=True, ex=86400)
    assert r.get("ocp:runner:idem:e1") == "claimed"
    assert r.exists("ocp:runner:idem:e1") == 1
    assert r.expire("ocp:runner:cooldown:api", 120) is False
    r.set("ocp:runner:cooldown:api", "1", ex=120)
    assert 0 < r.ttl("ocp:runner:cooldown:api") <= 120
    assert r.delete("ocp:runner:cooldown:api") == 1
    # Second-gate rate limit (sorted set of execution timestamps).
    key = "ocp:runner:ratelimit:api"
    r.zadd(key, {"e1": 1000.0, "e2": 2000.0})
    r.zremrangebyscore(key, 0, 1500)
    assert r.zcard(key) == 1


def test_backend_reaches_ocp_keys_but_not_dangerous_commands(clients: Clients) -> None:
    b = clients.backend
    b.xadd("ocp:events", {"event": "x"}, maxlen=10000, approximate=True)
    b.set("ocp:challenge:c1", "nonce", ex=120)
    assert b.getdel("ocp:challenge:c1") == "nonce"
    with pytest.raises(NoPermissionError):
        b.set("other:key", "x")
    for command in (("KEYS", "*"), ("FLUSHALL",), ("CONFIG", "GET", "*"), ("SAVE",)):
        with pytest.raises(NoPermissionError):
            send(b, command)


@pytest.mark.parametrize(
    ("backend_url", "runner_url"),
    [
        ("redis://backend:same@h:6379/0", "redis://runner:same@h:6379/0"),
        ("redis://admin:pw1@h:6379/0", "redis://runner:pw2@h:6379/0"),
        ("redis://backend:p@w@h:6379/0", "redis://runner:pw2@h:6379/0"),
        ("redis://backend:@h:6379/0", "redis://runner:pw2@h:6379/0"),
    ],
)
def test_start_script_refuses_bad_urls(backend_url: str, runner_url: str) -> None:
    name = run_redis(backend_url, runner_url)
    try:
        deadline = time.monotonic() + 20
        while docker("inspect", "-f", "{{.State.Running}}", name).stdout.strip() == "true":
            assert time.monotonic() < deadline, "redis started with a bad URL"
            time.sleep(0.3)
        assert docker("inspect", "-f", "{{.State.ExitCode}}", name).stdout.strip() != "0"
        logs = docker("logs", name, check=False)
        assert "same" not in logs.stdout + logs.stderr  # no password echoed
    finally:
        docker("rm", "-f", name, check=False)
