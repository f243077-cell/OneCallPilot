"""Compose policy over every profile (SEC-002, SEC-013, CLAUDE.md §4.8, §6.5, §6.6).

Renders the whole stack with the dummy ``.env.example`` values and checks:
backend-worker never receives ACTION_SIGNING_KEY; only backend-api publishes a
port on 0.0.0.0; only the two socket proxies mount the Docker socket; the
backend services drop all capabilities and run read-only; only they mount the
secrets folder, read-only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("testbed", "testbed-slots", "obs", "copilot", "runner", "tools", "backend")


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    argv = ["docker", "compose", "--env-file", ".env.example"]
    for profile in PROFILES:
        argv += ["--profile", profile]
    done = subprocess.run(  # noqa: S603 - fixed argv
        [*argv, "config", "--format", "json"], cwd=ROOT, capture_output=True, text=True,
        timeout=60, check=False,
    )  # fmt: skip
    if done.returncode != 0:
        pytest.skip(f"docker compose unavailable: {done.stderr.strip()[:200]}")
    rendered: dict[str, Any] = json.loads(done.stdout)["services"]
    return rendered


def test_worker_never_holds_the_action_signing_key(services: dict[str, Any]) -> None:
    worker = services["backend-worker"]["environment"]
    assert "ACTION_SIGNING_KEY" not in worker
    assert "RUNNER_LINK_KEY" in worker
    assert "ACTION_SIGNING_KEY" in services["backend-api"]["environment"]
    for name, service in services.items():
        if name not in ("backend-api", "runner"):
            assert "ACTION_SIGNING_KEY" not in (service.get("environment") or {}), name


def test_only_backend_api_is_published_on_all_interfaces(services: dict[str, Any]) -> None:
    for name, service in services.items():
        for port in service.get("ports") or []:
            host_ip = port.get("host_ip", "")
            if name == "backend-api":
                assert (host_ip, port["published"]) == ("0.0.0.0", "8000")
            else:
                assert host_ip == "127.0.0.1", f"{name} publishes {port}"


def test_only_the_proxies_mount_the_docker_socket(services: dict[str, Any]) -> None:
    for name, service in services.items():
        mounts = [v.get("source", "") for v in service.get("volumes") or []]
        if any("docker.sock" in m for m in mounts):
            assert name in ("socket-proxy-ro", "socket-proxy-rw"), name


def test_backend_services_are_locked_down(services: dict[str, Any]) -> None:
    for name in ("backend-api", "backend-worker"):
        service = services[name]
        assert service["read_only"] is True and service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
    worker = services["backend-worker"]["volumes"]
    assert [(v["target"], v.get("read_only")) for v in worker] == [
        ("/ledger", True),
        ("/run/secrets", True),
    ]
    api = services["backend-api"]["volumes"]
    assert [(v["target"], v.get("read_only")) for v in api] == [("/run/secrets", True)]


def test_only_the_backend_mounts_the_secrets_folder(services: dict[str, Any]) -> None:
    """The FCM service account lives there (FR-018); the runner and every other
    service must never see it (CLAUDE.md §6.6)."""
    secrets = ROOT / "infrastructure" / "secrets"
    for name, service in services.items():
        for volume in service.get("volumes") or []:
            if Path(volume.get("source", "")) == secrets:
                assert name in ("backend-api", "backend-worker"), name
                assert volume.get("read_only") is True, name
