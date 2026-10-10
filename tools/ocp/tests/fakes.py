"""A fake `docker`/`uv` for the ocp tests: canned answers by command prefix."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ocp_cli.stack import Result


def inspect(
    name: str,
    service: str,
    *,
    state: str = "running",
    health: str | None = "healthy",
    ports: dict[str, list[tuple[str, str]]] | None = None,
    mounts: Sequence[str] = (),
    project: bool = True,
) -> dict[str, Any]:
    """One `docker inspect` entry."""
    labels = {"com.docker.compose.service": service} if project else {}
    return {
        "Name": f"/{name}",
        "Config": {"Labels": labels},
        "State": {"Status": state, **({"Health": {"Status": health}} if health else {})},
        "HostConfig": {
            "PortBindings": {
                port: [{"HostIp": ip, "HostPort": hp} for ip, hp in hosts]
                for port, hosts in (ports or {}).items()
            }
        },
        "Mounts": [{"Type": "bind", "Source": m, "Destination": m} for m in mounts],
    }


class FakeDocker:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.services = ["cs-api-140-1", "cs-lb", "cs-redis"]
        self.images = {
            "cs-api-140-1": "chaos-shop/api:1.4.0",
            "cs-lb": "nginx:1.30.5-alpine",
            "cs-redis": "redis:7.4.11-alpine",
        }
        self.project: list[dict[str, Any]] = [
            inspect("cs-api-140-1", "cs-api-140-1"),
            inspect("cs-lb", "cs-lb", ports={"80/tcp": [("127.0.0.1", "8080")]}),
            inspect("cs-redis", "cs-redis"),
        ]
        self.others: list[dict[str, Any]] = []
        self.compose_version = "5.5.1"
        self.fail: dict[str, Result] = {}

    def __call__(self, command: Sequence[str], cwd: Path, capture: bool) -> Result:
        cmd = list(command)
        self.commands.append(cmd)
        joined = " ".join(cmd)
        for prefix, result in self.fail.items():
            if joined.startswith(prefix) or prefix in joined:
                return result
        if cmd[:4] == ["docker", "compose", "version", "--short"]:
            return Result(0, self.compose_version + "\n")
        if cmd[:2] == ["docker", "version"]:
            return Result(0, "29.1.0\n")
        if cmd[:2] == ["docker", "info"]:
            return Result(0, str(10 * (1 << 30)) + "\n")
        if "config" in cmd and "--services" in cmd:
            return Result(0, "\n".join(self.services) + "\n")
        if "config" in cmd and "json" in cmd:
            services = {name: {"image": image} for name, image in self.images.items()}
            return Result(0, json.dumps({"services": services}))
        if cmd[:3] == ["docker", "ps", "-aq"]:
            items = self.project if "--filter" in cmd else self.project + self.others
            return Result(0, "\n".join(item["Name"].lstrip("/") for item in items))
        if cmd[:2] == ["docker", "inspect"]:
            wanted = set(cmd[2:])
            everything = self.project + self.others
            return Result(0, json.dumps([i for i in everything if i["Name"].lstrip("/") in wanted]))
        return Result(0)

    def ran(self, *parts: str) -> bool:
        return any(all(p in c for p in parts) for c in self.commands)
