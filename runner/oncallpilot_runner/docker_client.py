"""Docker access through socket-proxy-rw only (architecture §2.10, §2.11, §7.4).

The runner never mounts the Docker socket: `DOCKER_HOST` is
`tcp://socket-proxy-rw:2375`, and `DOCKER_API_VERSION` is passed to the SDK as
`version=`, because the proxy blocks `/version` (docs/checks/proxy.md).

- `DockerReader` is the read-only view a dry run gets: it lists and inspects
  containers and returns plain values, never SDK objects, so dry-run code has no
  way to start, stop, restart, kill or remove anything (RUN-007).
- `DockerControl` adds the only two writes the runner makes so far: start and
  stop (graceful, with a timeout) of an existing container, by ID. Callers check
  the allowlist on a fresh inspect immediately before each write (§7.4). There
  is no create, remove, exec or pull here, and the proxy refuses them anyway.
"""

from dataclasses import dataclass
from typing import Any, Protocol

import docker
import requests
from docker.errors import DockerException

TIMEOUT_SECONDS = 10
STOP_TIMEOUT_SECONDS = 10  # SIGTERM, then SIGKILL after 10 s (C4)


class DockerUnavailable(RuntimeError):
    """Docker (through the proxy) did not answer, or refused the call."""


@dataclass(frozen=True)
class Container:
    name: str
    labels: dict[str, str]
    state: str  # C1 DockerState
    health: str  # C1 DockerHealth: healthy, unhealthy, starting, none
    id: str = ""
    restart_count: int = 0


class DockerReader(Protocol):
    def list_containers(self, labels: dict[str, str]) -> list[Container]:
        """Every container (running or not) that carries all these labels."""
        ...


class DockerControl(DockerReader, Protocol):
    def inspect(self, name: str) -> Container:
        """One container by name, freshly inspected."""
        ...

    def start(self, container_id: str) -> None: ...

    def stop(self, container_id: str, timeout: int = STOP_TIMEOUT_SECONDS) -> None: ...


class SdkDocker:
    """`DockerControl` over the Docker SDK: `GET /containers/json`,
    `GET /containers/{id}/json`, `POST /containers/{id}/start` and `/stop` only."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def connect(cls, base_url: str, api_version: str) -> "SdkDocker":
        return cls(docker.DockerClient(base_url=base_url, version=api_version,
                                       timeout=TIMEOUT_SECONDS + STOP_TIMEOUT_SECONDS))  # fmt: skip

    def list_containers(self, labels: dict[str, str]) -> list[Container]:
        filters = {"label": [f"{key}={value}" for key, value in sorted(labels.items())]}
        found = self._call(lambda: self.client.containers.list(all=True, filters=filters))
        return [_container(c.attrs) for c in found]

    def inspect(self, name: str) -> Container:
        return _container(self._call(lambda: self.client.api.inspect_container(name)))

    def start(self, container_id: str) -> None:
        self._call(lambda: self.client.api.start(container_id))

    def stop(self, container_id: str, timeout: int = STOP_TIMEOUT_SECONDS) -> None:
        self._call(lambda: self.client.api.stop(container_id, timeout=timeout))

    @staticmethod
    def _call(fn: Any) -> Any:
        try:
            return fn()
        except (DockerException, requests.exceptions.RequestException) as exc:
            raise DockerUnavailable(f"Docker did not answer: {type(exc).__name__}") from exc


def _container(attrs: dict[str, Any]) -> Container:
    state = attrs.get("State") or {}
    health = (state.get("Health") or {}).get("Status") or "none"
    labels = (attrs.get("Config") or {}).get("Labels") or {}
    restarts = attrs.get("RestartCount", 0)
    return Container(
        name=str(attrs.get("Name", "")).lstrip("/"),
        labels={str(k): str(v) for k, v in labels.items()},
        state=str(state.get("Status", "dead")),
        health=str(health),
        id=str(attrs.get("Id", "")),
        restart_count=restarts if isinstance(restarts, int) else 0,
    )
