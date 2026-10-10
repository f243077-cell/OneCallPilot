"""A2.3 end to end: dry runs by the real runner image against the real testbed (live).

Needs the stack from `ocp up` (testbed and socket-proxy-rw) and the built image:

    docker build --build-context contracts=contracts -t oncallpilot/runner:0.1.0 runner
    uv run pytest -m live -v tests/live/test_live_dry_run.py

What the module does, and undoes:
1. switches socket-proxy-rw to LOG_LEVEL=debug, so every request it receives is
   logged (RUN-007's acceptance: the proxy log of a dry run holds only GETs);
2. `chaos inject 2` (api 1.5.0 becomes active), so rollback_deploy has a
   release to go back to;
3. starts a throwaway Redis and the runner image the way compose will run it:
   on rw_proxy_net (the proxy) and chaos_net (cs-lb), DOCKER_HOST
   tcp://socket-proxy-rw:2375 with the host's pinned API version, no socket,
   and the deploy ledger mounted READ-ONLY;
4. at the end: removes them, `chaos reset`, and the proxy back to its log level.

Each test sends signed dry runs and checks the answer, then checks that nothing
changed: every testbed container (state, start time, PID, restart count), the
ledger, the runner's Redis keys, and the cache.
"""

import json
import os
import re
import secrets
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import redis

from oncallpilot_runner.fingerprint import state_fingerprint
from oncallpilot_runner.signing import Keys, verify
from tests.live.test_live_runner import IMAGE, REDIS_IMAGE, RUNNER_ACL, docker
from tests.publish_signed import build_request, entry, signed_for, vector_keys, vector_path

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[3]
REQUESTS, RESULTS = "ocp:runner:requests", "ocp:runner:results"
PROXY = "oncallpilot-socket-proxy-rw-1"
LEDGER_VOLUME = "oncallpilot_deploy_ledger"
SHOP = "http://127.0.0.1:8080"
REQUEST_LINE = re.compile(r'"([A-Z]+) (/\S*) HTTP/1\.[01]"')


def run(*args: str, env: dict[str, str] | None = None, timeout: int = 300) -> str:
    done = subprocess.run(
        list(args), capture_output=True, text=True, timeout=timeout, cwd=ROOT,
        env={**os.environ, **(env or {})},
    )  # fmt: skip
    if done.returncode != 0:
        raise AssertionError(f"{' '.join(args[:4])}…: {done.stderr.strip()[-500:]}")
    return done.stdout.strip()


def chaos(*args: str) -> str:
    return run("uv", "run", "--project", "chaos-shop/cli", "-q", "chaos", *args)


def proxy_log_level(level: str | None) -> None:
    env = {"SOCKET_PROXY_RW_LOG_LEVEL": level} if level else {}
    run("docker", "compose", "--profile", "runner", "up", "-d", "--wait", "socket-proxy-rw",
        env=env)  # fmt: skip


def env_file_value(name: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip()
    raise AssertionError(f"{name} is not in .env")


def containers() -> dict[str, tuple[str, str, int, int]]:
    """Every testbed container: state, start time, PID, restart count."""
    ids = docker("ps", "-aq", "--filter", "label=oncallpilot.managed=true").split()
    fmt = "{{.Name}}|{{.State.Status}}|{{.State.StartedAt}}|{{.State.Pid}}|{{.RestartCount}}"
    rows = docker("inspect", "--format", fmt, *ids).splitlines()
    out = {}
    for row in rows:
        name, state, started, pid, restarts = row.split("|")
        out[name.lstrip("/")] = (state, started, int(pid), int(restarts))
    return out


def ledger_text() -> str:
    return docker("run", "--rm", "-v", f"{LEDGER_VOLUME}:/l:ro", "busybox:1.37",
                  "cat", "/l/deploys.jsonl")  # fmt: skip


def cache_stats(token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{SHOP}/internal/cache/stats", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body: dict[str, Any] = json.loads(response.read())
    return body


@pytest.fixture(scope="module")
def keys() -> Keys:
    path = vector_path()
    if path is None:
        pytest.skip("signing vector not found (set OCP_CONTRACTS_DIR)")
    return vector_keys(path)


@pytest.fixture(scope="module")
def stack(keys: Keys) -> Iterator[dict[str, Any]]:
    if "running" not in docker("inspect", "-f", "{{.State.Status}}", "cs-lb", check=False):
        pytest.skip("the testbed is not running (ocp up)")
    tag = secrets.token_hex(3)
    net, rds, runner = f"ocp-dr-{tag}", f"ocp-dr-redis-{tag}", f"ocp-dr-runner-{tag}"
    runner_pw, backend_pw = secrets.token_hex(16), secrets.token_hex(16)
    api_version = docker("version", "--format", "{{.Server.APIVersion}}")
    token = env_file_value("RUNNER_ADMIN_TOKEN")
    try:
        proxy_log_level("debug")
        chaos("reset")
        chaos("inject", "2")
        docker("network", "create", net)
        docker(
            "run", "-d", "--name", rds, "--network", net, "--memory", "64m",
            "-p", "127.0.0.1::6379", REDIS_IMAGE,
            "redis-server", "--save", "", "--appendonly", "no", "--user", "default", "off",
            "--user", "runner", "on", f">{runner_pw}", *RUNNER_ACL,
            "--user", "backend", "on", f">{backend_pw}", "~*", "+@all",
        )  # fmt: skip
        port = docker("port", rds, "6379/tcp").splitlines()[0].rsplit(":", 1)[1]
        docker(
            "create", "--name", runner, "--hostname", "runner-1", "--network", net,
            "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "128m",
            "-v", f"{LEDGER_VOLUME}:/ledger:ro",
            "-e", f"RUNNER_REDIS_URL=redis://runner:{runner_pw}@{rds}:6379/0",
            "-e", f"ACTION_SIGNING_KEY={keys.action.hex()}",
            "-e", f"RUNNER_LINK_KEY={keys.link.hex()}",
            "-e", "LEDGER_PATH=/ledger/deploys.jsonl",
            "-e", "DOCKER_HOST=tcp://socket-proxy-rw:2375",
            "-e", f"DOCKER_API_VERSION={api_version}",
            "-e", f"RUNNER_ADMIN_TOKEN={token}",
            IMAGE,
        )  # fmt: skip
        docker("network", "connect", "oncallpilot_rw_proxy_net", runner)
        docker("network", "connect", "oncallpilot_chaos_net", runner)
        docker("start", runner)
        backend = redis.Redis(host="127.0.0.1", port=int(port), username="backend",
                              password=backend_pw, decode_responses=True)  # fmt: skip
        deadline = time.monotonic() + 60
        while docker("inspect", "-f", "{{.State.Health.Status}}", runner) != "healthy":
            assert time.monotonic() < deadline, docker("logs", runner, check=False)
            time.sleep(1)
        yield {"backend": backend, "runner": runner, "token": token}
    finally:
        print(docker("logs", runner, check=False))
        docker("rm", "-f", runner, rds, check=False)
        docker("network", "rm", net, check=False)
        chaos("reset")
        proxy_log_level(None)


def dry_runs(stack: dict[str, Any], keys: Keys, *calls: tuple[str, dict[str, Any]]) -> list[Any]:
    """Send signed dry runs; return their results in order, after checking that
    nothing changed and that the proxy saw only GET requests meanwhile."""
    backend: redis.Redis = stack["backend"]
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    before = (containers(), ledger_text(), set(backend.keys("ocp:runner:*")))
    sent = []
    for action, params in calls:
        message = signed_for(build_request("dry_run", action, params, now=datetime.now(UTC)), keys)
        backend.xadd(REQUESTS, entry(message))  # type: ignore[arg-type]
        sent.append(message["request_id"])
    deadline = time.monotonic() + 30
    while True:
        rows: Any = backend.xrange(RESULTS)
        answers = {json.loads(f["msg"])["request_id"]: json.loads(f["msg"]) for _, f in rows}
        if all(r in answers for r in sent):
            break
        assert time.monotonic() < deadline, f"{len(answers)} of {len(sent)} answered"
        time.sleep(0.5)
    after = (containers(), ledger_text(), set(backend.keys("ocp:runner:*")))
    assert after[0] == before[0], "a container changed"
    assert after[1] == before[1], "the ledger changed"
    assert after[2] == before[2] | {RESULTS}, "Redis keys changed"
    methods = REQUEST_LINE.findall(docker("logs", "--since", since, PROXY))
    assert methods, "the proxy logged no request (is LOG_LEVEL=debug?)"
    assert {m for m, _ in methods} == {"GET"}, methods
    results = [answers[r] for r in sent]
    for result in results:
        assert verify(result, keys.link) and not verify(result, keys.action)
        assert (result["type"], result["status"], result["error_code"]) == (
            "dry_run_result", "ok", None,
        )  # fmt: skip
    return [r["dry_run"] for r in results]


def changes(preview: dict[str, Any]) -> list[tuple[str, str]]:
    return [(c["target"], c["change"]) for c in preview["would_change"]]


def test_rollback_deploy_dry_run_end_to_end(stack: dict[str, Any], keys: Keys) -> None:
    """The thin slice's action: after scenario 2, roll api back from 1.5.0 to 1.4.0."""
    first, again = dry_runs(
        stack, keys,
        ("rollback_deploy", {"service": "api", "target_release": "1.4.0"}),
        ("rollback_deploy", {"service": "api", "target_release": "1.4.0"}),
    )  # fmt: skip
    assert changes(first) == [("cs-api-140-1", "start"), ("cs-api-150-1", "stop")]
    assert first["current"]["active_release"] == "1.5.0"
    assert first["current"]["running_replicas"] == 1
    assert first["preconditions"] == {
        "rate_limit_ok": True, "cooldown_ok": True, "slots_available": True,
    }  # fmt: skip
    assert first["state_fingerprint"] == state_fingerprint(
        "1.5.0", 1, ["cs-api-140-1", "cs-api-150-1"]
    )
    assert again["state_fingerprint"] == first["state_fingerprint"]  # same state, same value


def test_restart_scale_and_clear_cache_dry_runs_end_to_end(
    stack: dict[str, Any], keys: Keys
) -> None:
    token = stack["token"]
    catalog_before = cache_stats(token)["catalog"]["present"]
    since = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    worker, redis_, scale, catalog, pricing = dry_runs(
        stack, keys,
        ("restart_service", {"service": "worker"}),
        ("restart_service", {"service": "redis"}),
        ("scale_service", {"service": "api", "replicas": 3}),
        ("clear_cache", {"cache": "catalog"}),
        ("clear_cache", {"cache": "pricing"}),
    )  # fmt: skip
    assert changes(worker) == [("cs-worker-210", "restart")]
    assert worker["current"]["active_release"] == "2.1.0"
    assert changes(redis_) == [("cs-redis", "restart")]
    assert redis_["current"]["active_release"] is None
    assert redis_["state_fingerprint"] == state_fingerprint(None, None, ["cs-redis"])
    assert changes(scale) == [("cs-api-150-2", "start"), ("cs-api-150-3", "start")]
    assert scale["current"]["running_replicas"] == 1
    assert changes(catalog) == [("catalog", "clear")]
    assert catalog["warnings"] == [] and pricing["warnings"] == []
    assert catalog["current"]["cache_keys"] == (1 if catalog_before else 0)
    assert isinstance(pricing["current"]["cache_keys"], int)
    # The caches were read, never cleared: cs-api logs every clear (shop.admin).
    api_logs = docker("logs", "--since", since, "cs-api-150-1")
    assert "cache cleared" not in api_logs
    assert cache_stats(token)["catalog"]["present"] or not catalog_before
    used = docker("stats", "--no-stream", "--format", "{{.MemUsage}}", stack["runner"])
    print(f"runner memory after the dry runs: {used}")
