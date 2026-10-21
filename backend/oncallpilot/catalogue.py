"""The action catalogue and target allowlist, as the agent's validator reads them
(architecture §5.6 step 4, §7.2, §7.4; AI-018; ``CLAUDE.md`` §5.6, §6.4, §6.9).

The same files as the runner: ``contracts/actions.yaml`` (C4) and
``runner/targets.yaml``, plus the deploy ledger for ``values_from:
ledger_releases``. A proposal is valid only when:

- its action is in the catalogue with ``enabled: true``;
- its parameters are exactly the catalogue's, each an allowed enum value or
  an integer within bounds; ``ledger_releases`` = the service's releases in
  the ledger history that have slot containers, minus the active one;
- every ``live_check: targets`` value is a service of the target allowlist.

Risk tier, approval requirement and the rollback step template come from the
catalogue only; nothing the model says about them is used (§6.9 of
``CLAUDE.md``). Any file that cannot be read fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from oncallpilot.ledger import read_records

ParamValue = str | int


class CatalogueError(ValueError):
    """The catalogue, targets or ledger cannot be read: no proposal is accepted."""


@dataclass(frozen=True)
class ParamSpec:
    type: str  # enum | integer
    values: tuple[str, ...] | None = None
    values_from: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    live_check: str | None = None


@dataclass(frozen=True)
class ActionSpec:
    name: str
    enabled: bool
    risk_tier: str
    approval_requirement: str
    params: dict[str, ParamSpec]
    rollback_step: dict[str, Any] | None


@dataclass(frozen=True)
class Catalogue:
    version: str
    actions: dict[str, ActionSpec]

    @classmethod
    def load(cls, path: Path) -> Catalogue:
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            actions = {}
            for name, spec in data["actions"].items():
                params = {}
                for pname, p in (spec.get("params") or {}).items():
                    if p["type"] == "enum":
                        params[str(pname)] = ParamSpec(
                            "enum",
                            values=tuple(str(v) for v in p["values"]) if "values" in p else None,
                            values_from=p.get("values_from"),
                            live_check=p.get("live_check"),
                        )
                    elif p["type"] == "integer":
                        params[str(pname)] = ParamSpec(
                            "integer", minimum=int(p["minimum"]), maximum=int(p["maximum"])
                        )
                    else:
                        raise ValueError(f"unknown parameter type {p['type']!r}")
                actions[str(name)] = ActionSpec(
                    name=str(name),
                    enabled=spec["enabled"] is True,
                    risk_tier=str(spec["risk_tier"]),
                    approval_requirement=str(spec["approval_requirement"]),
                    params=params,
                    rollback_step=spec.get("rollback_step"),
                )
            return cls(str(data["catalogue_version"]), actions)
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError, ValueError) as exc:
            raise CatalogueError(f"cannot load {path}: {exc}") from exc


@dataclass(frozen=True)
class Targets:
    """Services of ``runner/targets.yaml`` and the releases that have slots."""

    releases: dict[str, tuple[str, ...]]

    @classmethod
    def load(cls, path: Path) -> Targets:
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            return cls(
                {
                    str(name): tuple(str(r) for r in spec.get("releases") or [])
                    for name, spec in data["services"].items()
                }
            )
        except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError) as exc:
            raise CatalogueError(f"cannot load {path}: {exc}") from exc


def ledger_releases(ledger: Path, targets: Targets, service: str) -> set[str]:
    """Rollback targets: the service's ledger releases that have slots, minus the active one."""
    records = [r for r in read_records(ledger) if r.service == service]
    if not records:
        return set()
    active = max(records, key=lambda r: r.deployed_at).release
    with_slots = set(targets.releases.get(service, ()))
    return {r.release for r in records if r.release in with_slots} - {active}


@dataclass(frozen=True)
class Validated:
    action: ActionSpec
    params: dict[str, ParamValue]
    catalogue_version: str


def validate(
    catalogue: Catalogue,
    targets: Targets,
    ledger: Path,
    action: str,
    params: dict[str, Any],
) -> tuple[Validated | None, list[str]]:
    """``(validated, [])`` or ``(None, problems)``."""
    spec = catalogue.actions.get(action)
    if spec is None or not spec.enabled:
        return None, [f"action {action!r} is not enabled in the catalogue"]
    if set(params) != set(spec.params):
        return None, [f"parameters {sorted(params)} are not exactly {sorted(spec.params)}"]
    problems: list[str] = []
    clean: dict[str, ParamValue] = {}
    for name, p in spec.params.items():
        value = params[name]
        if p.type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"{name} must be an integer")
            elif p.minimum is None or p.maximum is None or not p.minimum <= value <= p.maximum:
                problems.append(f"{name} must be between {p.minimum} and {p.maximum}")
            else:
                clean[name] = value
            continue
        if not isinstance(value, str):
            problems.append(f"{name} must be one of the allowed values")
            continue
        if p.values is not None:
            allowed = set(p.values)
        elif p.values_from == "ledger_releases":
            allowed = ledger_releases(ledger, targets, str(params.get("service", "")))
        else:
            raise CatalogueError(f"unknown values_from {p.values_from!r}")
        if value not in allowed:
            problems.append(f"{name} {value!r} is not allowed (allowed: {sorted(allowed)})")
            continue
        if p.live_check == "targets" and value not in targets.releases:
            problems.append(f"{name} {value!r} is not in the target allowlist")
            continue
        clean[name] = value
    if problems:
        return None, problems
    return Validated(spec, clean, catalogue.version), []


def rollback_step(
    spec: ActionSpec, params: dict[str, ParamValue], current: dict[str, ParamValue]
) -> dict[str, Any] | None:
    """The catalogue's rollback step with ``params.*`` and ``captured.*`` filled in.

    ``current`` holds what the dry run saw (``previous_release``,
    ``previous_replicas``), which is what the runner will capture.
    """
    template = spec.rollback_step
    if not template:
        return None
    filled: dict[str, ParamValue] = {}
    for name, source in template["params"].items():
        origin, _, key = str(source["from"]).partition(".")
        value = params.get(key) if origin == "params" else current.get(key)
        if value is None:
            return None
        filled[name] = value
    return {"action": template["action"], "params": filled}
