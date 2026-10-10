"""The Compose project on this host, driven through the `docker` CLI.

Every command goes through one `Runner`, so the tests replace it with a fake.
"""

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT = "oncallpilot"
# architecture §12.1: the default `ocp up` starts all four. A profile with no
# services yet (before its owner adds the file) selects nothing.
PROFILES = ("testbed", "obs", "copilot", "runner")
# Created stopped and started only by `chaos` and the runner (week-1.md).
SLOT_PROFILE = "testbed-slots"
COMPOSE_MIN = (2, 20)


class StackError(Exception):
    """The stack cannot be found or a Docker command failed."""


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


# (command, cwd, capture) -> Result. With capture=False the output goes to the
# terminal as it happens (builds, `chaos reset`).
Runner = Callable[[Sequence[str], Path, bool], Result]


def subprocess_runner(command: Sequence[str], cwd: Path, capture: bool) -> Result:
    # ocp itself may run inside `uv run`; a nested `uv run --project chaos-shop/cli`
    # must not inherit that virtual environment.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        done = subprocess.run(
            list(command), cwd=cwd, capture_output=capture, text=True, check=False, env=env
        )
    except FileNotFoundError as exc:
        raise StackError(f"{command[0]} is not installed or not on PATH") from exc
    return Result(done.returncode, done.stdout or "", done.stderr or "")


@dataclass(frozen=True)
class Container:
    """What `docker inspect` says about one container of the project."""

    name: str
    service: str
    state: str
    health: str  # healthy, unhealthy, starting, or "" without a health check
    port_bindings: list[tuple[str, str, str]] = field(default_factory=list)
    """(container port, host IP, host port); host IP "" means every interface."""
    mounts: list[str] = field(default_factory=list)
    """Host-side sources of bind mounts."""

    @classmethod
    def from_inspect(cls, data: dict[str, Any]) -> "Container":
        labels = data.get("Config", {}).get("Labels") or {}
        state = data.get("State") or {}
        bindings: list[tuple[str, str, str]] = []
        for port, hosts in (data.get("HostConfig", {}).get("PortBindings") or {}).items():
            for host in hosts or []:
                bindings.append((port, host.get("HostIp", ""), host.get("HostPort", "")))
        mounts = [m.get("Source", "") for m in data.get("Mounts") or [] if m.get("Type") == "bind"]
        return cls(
            name=str(data.get("Name", "")).lstrip("/"),
            service=labels.get("com.docker.compose.service", ""),
            state=state.get("Status", ""),
            health=(state.get("Health") or {}).get("Status", ""),
            port_bindings=bindings,
            mounts=mounts,
        )


class Stack:
    def __init__(self, root: Path, runner: Runner = subprocess_runner) -> None:
        self.root = root
        self.runner = runner

    @classmethod
    def discover(cls, start: Path | None = None, runner: Runner = subprocess_runner) -> "Stack":
        """The repository root: $OCP_ROOT, or the nearest folder upwards with
        the project's compose.yaml."""
        override = os.environ.get("OCP_ROOT")
        candidates = [Path(override)] if override else [start or Path.cwd()]
        if not override:
            candidates += list(candidates[0].resolve().parents)
        for folder in candidates:
            compose = folder / "compose.yaml"
            if compose.is_file() and f"name: {PROJECT}" in compose.read_text(encoding="utf-8"):
                return cls(folder.resolve(), runner)
        raise StackError(
            "cannot find the OnCallPilot compose.yaml; run ocp inside the repository "
            "or set OCP_ROOT"
        )

    # --- running commands ---

    def run(self, command: Sequence[str], *, capture: bool = True) -> Result:
        return self.runner(command, self.root, capture)

    def check(self, command: Sequence[str], *, capture: bool = True) -> Result:
        result = self.run(command, capture=capture)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-800:]
            raise StackError(f"`{' '.join(command)}` failed ({result.returncode}): {detail}")
        return result

    def compose(self, *profiles: str) -> list[str]:
        command = ["docker", "compose"]
        for profile in profiles:
            command += ["--profile", profile]
        return command

    # --- facts ---

    def compose_version(self) -> tuple[int, ...]:
        raw = self.check(["docker", "compose", "version", "--short"]).stdout.strip()
        numbers = raw.lstrip("v").split("-")[0].split(".")
        return tuple(int(n) for n in numbers if n.isdigit())

    def expected_services(self) -> list[str]:
        """Services the four profiles start (the slots are not among them)."""
        out = self.check([*self.compose(*PROFILES), "config", "--services"]).stdout
        return sorted(line.strip() for line in out.splitlines() if line.strip())

    def images(self) -> dict[str, str]:
        """service -> image of every service, slots included."""
        out = self.check(
            [*self.compose(*PROFILES, SLOT_PROFILE), "config", "--format", "json"]
        ).stdout
        services = json.loads(out).get("services", {})
        return {name: str(spec.get("image", "")) for name, spec in services.items()}

    def containers(self, *, project_only: bool = True) -> list[Container]:
        """Every container (running or not) of the project, or of the host."""
        command = ["docker", "ps", "-aq", "--no-trunc"]
        if project_only:
            command += ["--filter", f"label=com.docker.compose.project={PROJECT}"]
        ids = self.check(command).stdout.split()
        if not ids:
            return []
        data = json.loads(self.check(["docker", "inspect", *ids]).stdout)
        return [Container.from_inspect(item) for item in data]
