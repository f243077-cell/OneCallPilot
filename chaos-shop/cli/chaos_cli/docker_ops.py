"""Host Docker access for the chaos CLI (Docker SDK through Docker Desktop's named pipe).

The CLI is an operator tool with full Docker access (architecture §2.13). It only
starts, stops and restarts existing testbed containers and runs the helper; it
never execs into a monitored container.
"""

import json
import secrets
import subprocess
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docker
from docker.errors import NotFound
from docker.models.containers import Container

from chaos_cli.config import (
    HELPER_IMAGE,
    HELPER_NAME_PREFIX,
    LEDGER_DIR,
    LEDGER_VOLUME,
    NETWORK,
    REPO_ROOT,
)

STOP_TIMEOUT = 10
# Passed to `python -c` in the helper container; never imported on the host.
HELPER_SOURCE = Path(__file__).with_name("helper.py").read_text(encoding="utf-8")


class DockerOpsError(Exception):
    """A Docker step failed or did not finish in time."""


@dataclass(frozen=True)
class ContainerState:
    name: str
    exists: bool
    status: str = "missing"  # created, running, restarting, exited, ...
    health: str = ""  # healthy, unhealthy, starting, or "" without a health check
    restart_count: int = 0
    oom_killed: bool = False
    exit_code: int = 0
    started_at: str = ""
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.status in ("running", "restarting")


class Docker:
    def __init__(self, client: Any = None) -> None:
        self._client = client or docker.from_env(timeout=60)

    def _get(self, name: str) -> Container | None:
        try:
            return self._client.containers.get(name)
        except NotFound:
            return None

    def state(self, name: str) -> ContainerState:
        container = self._get(name)
        if container is None:
            return ContainerState(name=name, exists=False)
        attrs = container.attrs
        state = attrs["State"]
        labels = attrs["Config"].get("Labels") or {}
        return ContainerState(
            name=name,
            exists=True,
            status=state["Status"],
            health=(state.get("Health") or {}).get("Status", ""),
            restart_count=int(attrs.get("RestartCount", 0)),
            oom_killed=bool(state.get("OOMKilled")),
            exit_code=int(state.get("ExitCode", 0)),
            started_at=state.get("StartedAt", ""),
            labels={k: v for k, v in labels.items() if k.startswith("oncallpilot.")},
        )

    def states(self, names: Iterable[str]) -> dict[str, ContainerState]:
        return {name: self.state(name) for name in names}

    def _each(self, names: Iterable[str], action: str) -> None:
        def run(name: str) -> None:
            container = self._get(name)
            if container is None:
                raise DockerOpsError(f"container {name} does not exist")
            if action == "start":
                container.start()
            elif action == "stop":
                container.stop(timeout=STOP_TIMEOUT)
            else:
                container.restart(timeout=STOP_TIMEOUT)

        names = list(names)
        if not names:
            return
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            list(pool.map(run, names))

    def start(self, names: Iterable[str]) -> None:
        self._each(names, "start")

    def stop(self, names: Iterable[str]) -> None:
        """Graceful stop: SIGTERM, then SIGKILL after 10 s."""
        self._each(names, "stop")

    def restart(self, names: Iterable[str]) -> None:
        """Graceful restart: a stopped container is simply started."""
        names = list(names)
        stopped = [n for n in names if not self.state(n).running]
        self._each([n for n in names if n not in stopped], "restart")
        self._each(stopped, "start")

    def wait_healthy(self, names: Iterable[str], timeout: float) -> float:
        names = list(names)
        started = time.monotonic()
        while True:
            states = self.states(names)
            pending = [
                n for n, s in states.items() if not s.running or s.health not in ("healthy", "")
            ]
            if not pending:
                return time.monotonic() - started
            if time.monotonic() - started > timeout:
                raise DockerOpsError(f"not healthy after {timeout:.0f} s: {', '.join(pending)}")
            time.sleep(0.5)

    def compose_create_slots(self) -> None:
        """Create the stopped slots (profile testbed-slots); a no-op when they exist."""
        result = subprocess.run(
            ["docker", "compose", "--profile", "testbed-slots", "create"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise DockerOpsError(f"docker compose create failed: {result.stderr.strip()[-500:]}")

    def run_helper(self, plan: list[dict[str, Any]], token: str) -> list[Any]:
        """Run every step of one CLI command in one short-lived helper container."""
        try:
            output = self._client.containers.run(
                HELPER_IMAGE,
                command=["python", "-c", HELPER_SOURCE],
                name=HELPER_NAME_PREFIX + secrets.token_hex(4),
                environment={"OCP_PLAN": json.dumps(plan), "CHAOS_TOKEN": token},
                network=NETWORK,
                volumes={LEDGER_VOLUME: {"bind": LEDGER_DIR, "mode": "rw"}},
                remove=True,
                stdout=True,
                stderr=False,
                mem_limit="64m",
            )
        except docker.errors.ContainerError as exc:
            raise DockerOpsError(f"helper failed: {str(exc)[-500:]}") from exc
        results: list[Any] = json.loads(output)["results"]
        return results
