"""Target allowlist (runner/targets.yaml, architecture §7.4, RUN-004).

`container_allowed` is the check every Docker call must pass immediately
before it is made (handlers arrive in tasks A2.3 and A2.4): the container
matches its service's labels AND the name regex.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class TargetsError(ValueError):
    """runner/targets.yaml is missing or malformed: the runner refuses to start."""


@dataclass(frozen=True)
class Service:
    labels: dict[str, str]
    releases: tuple[str, ...]


@dataclass(frozen=True)
class Targets:
    name_regex: re.Pattern[str]
    services: dict[str, Service]

    @classmethod
    def load(cls, path: Path) -> "Targets":
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            services = {
                str(name): Service(
                    labels={str(k): str(v) for k, v in spec["labels"].items()},
                    releases=tuple(str(r) for r in spec.get("releases") or []),
                )
                for name, spec in data["services"].items()
            }
            regex = re.compile(str(data["name_regex"]))
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError, re.error) as exc:
            raise TargetsError(f"cannot load {path}: {exc}") from exc
        for name, service in services.items():
            if service.labels.get("oncallpilot.service") != name:
                raise TargetsError(f"{path}: service {name} must select oncallpilot.service={name}")
        return cls(regex, services)

    def allows_service(self, service: str) -> bool:
        return service in self.services

    def container_allowed(self, service: str, name: str, labels: dict[str, str]) -> bool:
        """Both checks of §7.4; a container failing either is never touched."""
        spec = self.services.get(service)
        if spec is None or not self.name_regex.fullmatch(name):
            return False
        return all(labels.get(key) == value for key, value in spec.labels.items())
