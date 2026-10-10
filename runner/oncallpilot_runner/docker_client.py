"""Docker access through socket-proxy-rw only (architecture §2.10, §2.11, §7.4).

The runner never mounts the Docker socket: `DOCKER_HOST` is
`tcp://socket-proxy-rw:2375`, and `DOCKER_API_VERSION` is passed to the SDK as
`version=`, because the proxy blocks `/version` (docs/checks/proxy.md).

`DockerReader` is the read-only view a dry run gets: it lists containers by
label and returns plain values, never SDK objects, so dry-run code has no way to
start, stop, restart, kill or remove anything (RUN-007). Write calls arrive with
the execute handlers (A2.4).
"""

from dataclasses import dataclass
from typing import Any, Protocol

import docker
import requests
from docker.errors import DockerException

TIMEOUT_SECONDS = 10


class DockerUnavailable(RuntimeError):
    """Docker (through the proxy) did not answer: the dry run is refused."""


@dataclass(frozen=True)
class Container:
    name: str
    labels: dict[str, str]
    state: str  # C1 DockerState
    health: str  # C1 DockerHealth: healthy, unhealthy, starting, none


class DockerReader(Protocol):
    def list_containers(self, labels: dict[str, str]) -> list[Container]:
        """Every container (running or not) that carries all these labels."""
        ...


class SdkDockerReader:
    """`DockerReader` over the Docker SDK: `GET /containers/json` and
    `GET /containers/{id}/json` only."""

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def connect(cls, base_url: str, api_version: str) -> "SdkDockerReader":
        return cls(docker.DockerClient(base_url=base_url, version=api_version,
                                       timeout=TIMEOUT_SECONDS))  # fmt: skip

    def list_containers(self, labels: dict[str, str]) -> list[Container]:
        filters = {"label": [f"{key}={value}" for key, value in sorted(labels.items())]}
        try:
            found = self.client.containers.list(all=True, filters=filters)
            return [_container(c.attrs) for c in found]
        except (DockerException, requests.exceptions.RequestException) as exc:
            raise DockerUnavailable(f"Docker did not answer: {type(exc).__name__}") from exc


def _container(attrs: dict[str, Any]) -> Container:
    state = attrs.get("State") or {}
    health = (state.get("Health") or {}).get("Status") or "none"
    labels = (attrs.get("Config") or {}).get("Labels") or {}
    return Container(
        name=str(attrs.get("Name", "")).lstrip("/"),
        labels={str(k): str(v) for k, v in labels.items()},
        state=str(state.get("Status", "dead")),
        health=str(health),
    )
