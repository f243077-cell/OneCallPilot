"""The action catalogue (contracts/actions.yaml, C4; RUN-003, RUN-018).

The runner loads the same file as the agent validator. An action is allowed
only if it is in the file with `enabled: true` AND has a registered handler.
Parameters must be exactly the catalogue's, each an enum value or a bounded
integer; anything else is refused before any Docker call.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class CatalogueError(ValueError):
    """The catalogue is missing or malformed: the runner refuses to start."""


@dataclass(frozen=True)
class Param:
    type: str  # enum | integer
    values: tuple[str, ...] | None = None
    values_from: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    live_check: str | None = None


@dataclass(frozen=True)
class Action:
    name: str
    enabled: bool
    params: dict[str, Param]


@dataclass(frozen=True)
class Catalogue:
    version: str
    actions: dict[str, Action]

    @classmethod
    def load(cls, path: Path) -> "Catalogue":
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            actions = {
                str(name): Action(
                    name=str(name),
                    enabled=spec["enabled"] is True,
                    params={str(p): _param(s) for p, s in (spec.get("params") or {}).items()},
                )
                for name, spec in data["actions"].items()
            }
            version = str(data["catalogue_version"])
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError, ValueError) as exc:
            raise CatalogueError(f"cannot load {path}: {exc}") from exc
        return cls(version, actions)

    @property
    def enabled(self) -> frozenset[str]:
        return frozenset(name for name, action in self.actions.items() if action.enabled)


def _param(spec: dict[str, Any]) -> Param:
    kind = spec["type"]
    if kind == "enum":
        if "values" in spec:
            return Param(kind, values=tuple(str(v) for v in spec["values"]),
                         live_check=spec.get("live_check"))  # fmt: skip
        return Param(kind, values_from=str(spec["values_from"]), live_check=spec.get("live_check"))
    if kind == "integer":
        return Param(kind, minimum=int(spec["minimum"]), maximum=int(spec["maximum"]))
    raise ValueError(f"unknown parameter type {kind!r}")


def param_problems(
    action: Action,
    params: dict[str, str | int],
    runtime_values: Callable[[str, dict[str, str | int]], Iterable[str]],
) -> list[str]:
    """Why ``params`` are not valid for ``action``; empty when they are.

    ``runtime_values(source, params)`` returns the allowed values of a
    ``values_from`` enum (``ledger_releases``); it raises when they cannot be
    read, so the caller refuses (fail closed).
    """
    problems = []
    if set(params) != set(action.params):
        problems.append(f"parameters {sorted(params)} are not exactly {sorted(action.params)}")
        return problems
    for name, spec in action.params.items():
        value = params[name]
        if spec.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"{name} must be an integer")
            elif (
                spec.minimum is None
                or spec.maximum is None
                or not (spec.minimum <= value <= spec.maximum)
            ):
                problems.append(f"{name} must be between {spec.minimum} and {spec.maximum}")
            continue
        if not isinstance(value, str):
            problems.append(f"{name} must be one of the allowed values")
            continue
        allowed = (
            set(spec.values) if spec.values is not None
            else set(runtime_values(spec.values_from or "", params))
        )  # fmt: skip
        if value not in allowed:
            problems.append(f"{name} is not an allowed value")
    return problems
