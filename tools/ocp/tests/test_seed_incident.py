"""ocp seed-incident (B1.4) with a fake docker."""

import argparse
from pathlib import Path

import pytest

from ocp_cli.commands import seed_incident
from ocp_cli.stack import Result, Stack, StackError
from tests.fakes import FakeDocker


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "compose.yaml").write_text("name: oncallpilot\ninclude: []\n", encoding="utf-8")
    (tmp_path / ".env").write_text("REDIS_URL=x\n", encoding="utf-8")
    timelines = tmp_path / "contracts" / "fixtures" / "timelines"
    timelines.mkdir(parents=True)
    (timelines / "bad_deploy_success.json").write_text("{}", encoding="utf-8")
    return tmp_path


def args(timeline: str, **overrides: object) -> argparse.Namespace:
    values = {"timeline": timeline, "new_ids": False, "realtime": False, "no_build": False}
    return argparse.Namespace(**{**values, **overrides})


def test_seeds_a_named_fixture_in_the_backend_image(root: Path) -> None:
    docker = FakeDocker()
    assert seed_incident.run(args("bad_deploy_success"), Stack(root, docker)) == 0
    (command,) = docker.commands
    folder = root / "contracts" / "fixtures" / "timelines"
    assert command[:2] == ["docker", "compose"]
    assert command[command.index("run") :] == [
        "run",
        "--rm",
        "--build",
        "-v",
        f"{folder.resolve()}:/timelines:ro",
        "backend-seed",
        "/timelines/bad_deploy_success.json",
    ]
    assert {"copilot", "tools"} <= set(command)


def test_path_and_flags(root: Path, tmp_path: Path) -> None:
    own = tmp_path / "mine" / "custom.json"
    own.parent.mkdir()
    own.write_text("{}", encoding="utf-8")
    docker = FakeDocker()
    run_args = args(str(own), new_ids=True, realtime=True, no_build=True)
    assert seed_incident.run(run_args, Stack(root, docker)) == 0
    (command,) = docker.commands
    assert "--build" not in command
    assert command[-4:] == ["backend-seed", "/timelines/custom.json", "--new-ids", "--realtime"]
    assert f"{own.parent.resolve()}:/timelines:ro" in command


def test_unknown_timeline_lists_the_fixtures(root: Path) -> None:
    with pytest.raises(StackError, match="bad_deploy_success"):
        seed_incident.run(args("nope"), Stack(root, FakeDocker()))


def test_needs_an_env_file(root: Path) -> None:
    (root / ".env").unlink()
    with pytest.raises(StackError, match=".env"):
        seed_incident.run(args("bad_deploy_success"), Stack(root, FakeDocker()))


def test_a_failed_seed_is_an_error(root: Path) -> None:
    docker = FakeDocker()
    docker.fail["backend-seed"] = Result(1, "", "boom")
    with pytest.raises(StackError, match="boom"):
        seed_incident.run(args("bad_deploy_success"), Stack(root, docker))
