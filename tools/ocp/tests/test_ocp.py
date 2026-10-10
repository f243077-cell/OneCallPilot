"""ocp up, down and doctor against a fake Docker (S1.1, SEC-013, NFR-007)."""

import argparse
from pathlib import Path
from typing import Any

import pytest

from ocp_cli import main as entry
from ocp_cli import policy
from ocp_cli.commands import doctor, down, up
from ocp_cli.stack import PROFILES, SLOT_PROFILE, Container, Result, Stack, StackError
from tests.fakes import FakeDocker, inspect


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "compose.yaml").write_text("name: oncallpilot\ninclude: []\n", encoding="utf-8")
    (tmp_path / ".env").write_text("CHAOS_TOKEN=x\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def docker() -> FakeDocker:
    return FakeDocker()


@pytest.fixture
def stack(root: Path, docker: FakeDocker) -> Stack:
    return Stack(root, docker)


def up_args(**overrides: object) -> argparse.Namespace:
    return argparse.Namespace(**{"no_build": False, "timeout": 5.0, **overrides})


# --- entry point ---


def test_every_command_module_is_a_subcommand() -> None:
    modules = entry.command_modules()
    assert {"up", "down", "doctor"} <= set(modules)
    for name, module in modules.items():
        assert module.NAME == name and module.HELP and callable(module.run)
    parsed = entry.parser(modules).parse_args(["up", "--no-build"])
    assert parsed.command == "up" and parsed.no_build is True


def test_the_root_is_found_from_a_subfolder(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCP_ROOT", raising=False)
    deep = root / "mobile" / "lib"
    deep.mkdir(parents=True)
    assert Stack.discover(deep).root == root.resolve()


def test_no_root_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCP_ROOT", raising=False)
    (tmp_path / "compose.yaml").write_text("name: someone-else\n", encoding="utf-8")
    with pytest.raises(StackError, match="OCP_ROOT"):
        Stack.discover(tmp_path)


# --- up ---


def test_up_starts_all_profiles_creates_slots_resets_and_prints_the_url(
    stack: Stack,
    docker: FakeDocker,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(up, "lan_address", lambda: "192.168.1.20")
    assert up.run(up_args(), stack) == 0
    start = next(c for c in docker.commands if "up" in c)
    for profile in PROFILES:
        assert ["--profile", profile] == start[start.index(profile) - 1 : start.index(profile) + 1]
    assert SLOT_PROFILE not in start  # the slots are created, never started
    assert {"-d", "--wait", "--build"} <= set(start)
    order = [
        docker.commands.index(start),
        next(i for i, c in enumerate(docker.commands) if c[-1] == "create"),
        next(i for i, c in enumerate(docker.commands) if c[-2:] == ["chaos", "reset"]),
    ]
    assert order == sorted(order)
    create = docker.commands[order[1]]
    assert create == ["docker", "compose", "--profile", SLOT_PROFILE, "create"]
    out = capsys.readouterr().out
    assert "3 services healthy" in out
    assert "API_BASE_URL=http://192.168.1.20:8000  (backend-api is not in the stack yet)" in out
    assert "WS_URL=ws://192.168.1.20:8000/ws/incidents" in out


def test_up_without_build_and_without_env(stack: Stack, docker: FakeDocker, root: Path) -> None:
    assert up.run(up_args(no_build=True), stack) == 0
    assert docker.ran("up", "--no-build") and not docker.ran("up", "--build")
    (root / ".env").unlink()
    with pytest.raises(StackError, match=".env"):
        up.run(up_args(), stack)


def test_up_refuses_an_old_compose(stack: Stack, docker: FakeDocker) -> None:
    docker.compose_version = "2.19.1"
    with pytest.raises(StackError, match="older than 2.20"):
        up.run(up_args(), stack)


def test_up_stops_when_a_step_fails(stack: Stack, docker: FakeDocker) -> None:
    docker.fail["chaos reset"] = Result(2, "", "chaos: no CHAOS_TOKEN")
    with pytest.raises(StackError, match="CHAOS_TOKEN"):
        up.run(up_args(), stack)


def test_up_reports_services_that_never_get_healthy(
    stack: Stack,
    docker: FakeDocker,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(up, "POLL_SECONDS", 0.01)
    docker.project[2] = inspect("cs-redis", "cs-redis", health="starting")
    assert up.run(up_args(timeout=0.05), stack) == 1
    assert "cs-redis is starting" in capsys.readouterr().out


# --- down ---


def test_down_removes_every_profile_including_slots_and_keeps_volumes(
    stack: Stack, docker: FakeDocker
) -> None:
    assert down.run(argparse.Namespace(), stack) == 0
    (command,) = docker.commands
    assert command[-1] == "down" and "--volumes" not in command and "-v" not in command
    assert set(PROFILES) | {SLOT_PROFILE} <= set(command)


# --- doctor ---


def test_doctor_passes_on_the_baseline(
    stack: Stack, docker: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    docker.others = [
        inspect("other-db", "", ports={"5432/tcp": [("127.0.0.1", "5432")]}, project=False)
    ]
    assert doctor.run(argparse.Namespace(), stack) == 0
    out = capsys.readouterr().out
    assert "cs-lb 127.0.0.1:8080" in out and "all checks passed" in out


@pytest.mark.parametrize(
    ("container", "message"),
    [
        (inspect("cs-lb", "cs-lb", ports={"80/tcp": [("", "8080")]}), "allowed is 127.0.0.1"),
        (inspect("cs-lb", "cs-lb", ports={"80/tcp": [("127.0.0.1", "80")]}), "port 8080"),
        (
            inspect("cs-redis", "cs-redis", ports={"6379/tcp": [("127.0.0.1", "6379")]}),
            "may publish nothing",
        ),
        (inspect("cs-redis", "cs-redis", mounts=["/var/run/docker.sock"]), "Docker socket"),
        (inspect("cs-redis", "cs-redis", state="exited", health=None), "cs-redis is exited"),
        (inspect("cs-redis", "cs-redis", health=None), "no health check"),
    ],
)
def test_doctor_fails_on_policy_breaks(
    stack: Stack,
    docker: FakeDocker,
    capsys: pytest.CaptureFixture[str],
    container: dict[str, Any],
    message: str,
) -> None:
    name = str(container["Name"]).lstrip("/")
    docker.project = [c for c in docker.project if c["Name"] != f"/{name}"] + [container]
    assert doctor.run(argparse.Namespace(), stack) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and message in out


def test_doctor_fails_on_unpinned_images_and_warns_on_other_lan_ports(
    stack: Stack, docker: FakeDocker, capsys: pytest.CaptureFixture[str]
) -> None:
    docker.others = [
        inspect("someone-web", "", ports={"80/tcp": [("0.0.0.0", "80")]}, project=False)
    ]
    assert doctor.run(argparse.Namespace(), stack) == 0  # a warning does not fail
    assert "WARN  no other container" in capsys.readouterr().out
    docker.others[0]["State"]["Status"] = "exited"  # stopped: exposes nothing
    assert doctor.run(argparse.Namespace(), stack) == 0
    assert "OK    no other container" in capsys.readouterr().out
    docker.images["cs-redis"] = "redis:latest"
    assert doctor.run(argparse.Namespace(), stack) == 1
    assert "cs-redis uses redis:latest" in capsys.readouterr().out


# --- policy ---


def test_backend_api_is_the_only_service_open_to_the_lan() -> None:
    api = Container("backend-api", "backend-api", "running", "healthy", [("8000/tcp", "", "8000")])
    grafana = Container("grafana", "grafana", "running", "healthy", [("3000/tcp", "", "3000")])
    assert policy.port_problems([api]) == []
    assert policy.port_problems([grafana]) != []
    assert policy.lan_exposed([api, grafana]) == [
        "backend-api 8000->8000/tcp",
        "grafana 3000->3000/tcp",
    ]


@pytest.mark.parametrize(
    ("image", "ok"),
    [
        ("nginx:1.30.5-alpine", True),
        ("chaos-shop/api:1.4.0", True),
        ("python@sha256:" + "a" * 64, True),
        ("redis", False),
        ("redis:latest", False),
        ("registry.local:5000/redis", False),
    ],
)
def test_pinned_images(image: str, ok: bool) -> None:
    assert (policy.image_problems({"s": image}) == []) is ok
