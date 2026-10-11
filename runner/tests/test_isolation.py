"""RUN-016: the runner's isolation, at config level (A2.5).

The intended service (tests/runner_service.yml) must satisfy the policy in
tests/isolation.py, and every way of loosening it must be caught. Once the
`runner` service is in infrastructure/compose/runner.yml it is checked too.
The running container is checked in tests/live/test_live_limits.py.
"""

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.isolation import FORBIDDEN_ENV, service_problems

HERE = Path(__file__).resolve().parent
SPEC = HERE / "runner_service.yml"
COMPOSE = HERE.parents[1] / "infrastructure" / "compose" / "runner.yml"
DOCKERFILE = HERE.parent / "Dockerfile"


def spec() -> dict[str, Any]:
    service: dict[str, Any] = yaml.safe_load(SPEC.read_text(encoding="utf-8"))["runner"]
    return service


def test_the_intended_service_is_isolated() -> None:
    assert service_problems(spec()) == []


def test_the_compose_runner_service_is_isolated_once_it_exists() -> None:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    if "runner" not in services:
        pytest.skip("not in runner.yml yet (waits for copilot-redis)")
    assert service_problems(services["runner"]) == []  # pragma: no cover


def test_the_image_runs_as_uid_10001() -> None:
    lines = [line.split() for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()]
    users = [words[1] for words in lines if words[:1] == ["USER"]]
    assert users == ["runner"]  # the last (and only) USER: never root
    assert any("--uid" in words and "10001" in words and "runner" in words for words in lines)


def _set(key: str, value: Any) -> Callable[[dict[str, Any]], None]:
    return lambda s: s.__setitem__(key, value)


def _env(key: str, value: str) -> Callable[[dict[str, Any]], None]:
    return lambda s: s["environment"].__setitem__(key, value)


LOOSENED: dict[str, Callable[[dict[str, Any]], None]] = {
    "root user": _set("user", "0:0"),
    "root by name": _set("user", "root"),
    "no user": lambda s: s.pop("user"),
    "writable root": _set("read_only", False),
    "no read_only": lambda s: s.pop("read_only"),
    "extra tmpfs": _set("tmpfs", ["/tmp", "/app"]),
    "no cap_drop": lambda s: s.pop("cap_drop"),
    "partial cap_drop": _set("cap_drop", ["NET_RAW"]),
    "cap_add": _set("cap_add", ["NET_ADMIN"]),
    "no no-new-privileges": _set("security_opt", []),
    "privileged": _set("privileged", True),
    "pid host": _set("pid", "host"),
    "network_mode host": _set("network_mode", "host"),
    "devices": _set("devices", ["/dev/kmsg"]),
    "published port": _set("ports", ["127.0.0.1:9000:9000"]),
    "env_file": _set("env_file", [".env"]),
    "more memory": _set("mem_limit", "256m"),
    "no memory limit": lambda s: s.pop("mem_limit"),
    "socket mount": lambda s: s["volumes"].append("/var/run/docker.sock:/var/run/docker.sock"),
    "socket instead of the ledger": _set("volumes", ["/var/run/docker.sock:/ledger"]),
    "extra mount": lambda s: s["volumes"].append("./runner:/app"),
    "extra network": lambda s: s["networks"].append("obs_net"),
    "unix DOCKER_HOST": _env("DOCKER_HOST", "unix:///var/run/docker.sock"),
    "read-only proxy": _env("DOCKER_HOST", "tcp://socket-proxy-ro:2375"),
    "missing DOCKER_HOST": lambda s: s["environment"].pop("DOCKER_HOST"),
    **{f"receives {name}": _env(name, "x") for name in FORBIDDEN_ENV},
    "an unlisted variable": _env("DEBUG", "1"),
}


@pytest.mark.parametrize("loosen", LOOSENED.values(), ids=LOOSENED.keys())
def test_every_loosening_is_caught(loosen: Callable[[dict[str, Any]], None]) -> None:
    service = copy.deepcopy(spec())
    loosen(service)
    assert service_problems(service) != []


def test_the_list_form_of_environment_is_checked_too() -> None:
    service = spec()
    service["environment"] = [f"{k}={v}" for k, v in service["environment"].items()]
    assert service_problems(service) == []
    service["environment"].append("DATABASE_URL=postgres://x")
    assert "receives DATABASE_URL" in service_problems(service)
