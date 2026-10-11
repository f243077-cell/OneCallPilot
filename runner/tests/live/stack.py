"""The live-test stack around the real testbed (A2.3 dry runs, A2.4 execute).

`runner_stack()` switches socket-proxy-rw to LOG_LEVEL=debug, runs `chaos reset`
and `chaos inject 2` (api 1.5.0 active, so rollback_deploy has a target), and
starts a throwaway Redis plus the runner image the way compose will run it: on
rw_proxy_net (the proxy) and chaos_net (cs-lb, probes), DOCKER_HOST
tcp://socket-proxy-rw:2375, no socket, read-only root, 128 MiB. On exit it
removes them, runs `chaos reset` and puts the proxy back to its log level.

The Docker API version is the host daemon's own (`docker version`), never a
fixed value: GitHub's runner serves at most 1.48 (the CI failure of 2026-10-10).

The deploy ledger volume is mounted read-only for dry runs. The execute test
mounts it read-write, as the runner service will (docs/checks/week-3.md, A2.4a).
"""

import os
import secrets
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import redis

from oncallpilot_runner.signing import Keys
from tests.live.test_live_runner import IMAGE, REDIS_IMAGE, RUNNER_ACL, docker

ROOT = Path(__file__).resolve().parents[3]
PROXY = "oncallpilot-socket-proxy-rw-1"
LEDGER_VOLUME = "oncallpilot_deploy_ledger"


def run(*args: str, env: dict[str, str] | None = None, timeout: int = 300, cwd: Path = ROOT) -> str:
    done = subprocess.run(
        list(args), capture_output=True, text=True, timeout=timeout, cwd=cwd,
        env={**os.environ, **(env or {})},
    )  # fmt: skip
    if done.returncode != 0:
        raise AssertionError(f"{' '.join(args[:4])}…: {done.stderr.strip()[-500:]}")
    return done.stdout.strip()


def chaos(*args: str) -> str:
    return run("uv", "run", "--project", "chaos-shop/cli", "-q", "chaos", *args)


def host_api_version() -> str:
    return docker("version", "--format", "{{.Server.APIVersion}}")


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


def ledger_text() -> str:
    return docker("run", "--rm", "-v", f"{LEDGER_VOLUME}:/l:ro", "busybox:1.37",
                  "cat", "/l/deploys.jsonl")  # fmt: skip


@contextmanager
def runner_stack(keys: Keys, *, ledger_writable: bool) -> Iterator[dict[str, Any]]:
    if "running" not in docker("inspect", "-f", "{{.State.Status}}", "cs-lb", check=False):
        pytest.skip("the testbed is not running (ocp up)")
    tag = secrets.token_hex(3)
    net, rds, runner = f"ocp-lv-{tag}", f"ocp-lv-redis-{tag}", f"ocp-lv-runner-{tag}"
    runner_pw, backend_pw = secrets.token_hex(16), secrets.token_hex(16)
    token = env_file_value("RUNNER_ADMIN_TOKEN")
    mount = f"{LEDGER_VOLUME}:/ledger" + ("" if ledger_writable else ":ro")
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
            "-v", mount,
            "-e", f"RUNNER_REDIS_URL=redis://runner:{runner_pw}@{rds}:6379/0",
            "-e", f"ACTION_SIGNING_KEY={keys.action.hex()}",
            "-e", f"RUNNER_LINK_KEY={keys.link.hex()}",
            "-e", "LEDGER_PATH=/ledger/deploys.jsonl",
            "-e", "DOCKER_HOST=tcp://socket-proxy-rw:2375",
            "-e", f"DOCKER_API_VERSION={host_api_version()}",
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
        yield {
            "backend": backend,
            "backend_url": f"redis://backend:{backend_pw}@127.0.0.1:{port}/0",
            "runner": runner,
            "token": token,
        }
    finally:
        print(docker("logs", runner, check=False))
        docker("rm", "-f", runner, rds, check=False)
        docker("network", "rm", net, check=False)
        chaos("reset")
        proxy_log_level(None)
