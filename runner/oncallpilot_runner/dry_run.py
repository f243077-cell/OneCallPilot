"""Dry runs of the four enabled actions (RUN-007, architecture §7.5, C5).

A dry run changes nothing. It reads the deploy ledger, lists containers through
`DockerReader` (GET only), reads the runner's own rate-limit and cooldown
counters, and for clear_cache calls the store's cache stats endpoint. It returns
C1's `DryRunResult` with the state fingerprint (`fingerprint.py`).

Targets (ruled 2026-10-10). N is the desired replica count from the ledger's
latest record for the service, never the running count:

- restart_service: slots 1..N of the active release. A service with no ledger
  record (redis) targets its allowlisted containers, with release and replicas
  null.
- scale_service: slots 1..max(N, replicas) of the active release.
- rollback_deploy: slots 1..N of the active release and slots 1..N of
  `target_release`.
- clear_cache: no containers. Its limits count under service `api`.

Every container must pass the allowlist (labels AND name regex, runner/
targets.yaml) at the moment it is listed; one that fails is never a target.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from oncallpilot_runner import ledger
from oncallpilot_runner.docker_client import Container, DockerReader, DockerUnavailable
from oncallpilot_runner.fingerprint import state_fingerprint
from oncallpilot_runner.limits import (
    CLEAR_CACHE_LIMITS_SERVICE,
    RATE_LIMIT,
    RATE_WINDOW_SECONDS,
    cooldown_key,
    rate_key,
)
from oncallpilot_runner.request import Request
from oncallpilot_runner.shop_admin import CacheStatsUnavailable
from oncallpilot_runner.targets import Targets

RELEASE_LABEL = "oncallpilot.release"
REPLICA_LABEL = "oncallpilot.replica"
RESTARTABLE = ("running", "restarting", "paused")
EFFECT_MAX = 300


class DryRunUnavailable(RuntimeError):
    """The state cannot be read (Docker or the ledger): the dry run is refused."""


class Counters(Protocol):
    """The runner's own limit counters (architecture §5.8, read only here)."""

    def zcount(self, key: str, min_score: float, max_score: float) -> int: ...
    def exists(self, key: str) -> bool: ...


@dataclass
class DryRunner:
    docker: DockerReader
    targets: Targets
    ledger_path: Path
    counters: Counters
    cache_stats: Callable[[], dict[str, Any]]
    now: Callable[[], datetime]

    def run(self, request: Request) -> dict[str, Any]:
        params = request.params
        try:
            if request.action == "rollback_deploy":
                return self._rollback(str(params["service"]), str(params["target_release"]))
            if request.action == "restart_service":
                return self._restart(str(params["service"]))
            if request.action == "scale_service":
                return self._scale(str(params["service"]), int(params["replicas"]))
            if request.action == "clear_cache":
                return self._clear_cache(str(params["cache"]))
        except (DockerUnavailable, ledger.LedgerError) as exc:
            raise DryRunUnavailable(str(exc)) from exc
        raise DryRunUnavailable(f"no dry run for {request.action}")

    # --- the four actions ----------------------------------------------------

    def _rollback(self, service: str, target_release: str) -> dict[str, Any]:
        active = ledger.active(self.ledger_path, service)
        if active.release is None or active.replicas is None:
            raise DryRunUnavailable(f"the ledger has no active release for {service}")
        n = active.replicas
        containers = self.containers(service)
        old = slots(containers, active.release, n)
        new = slots(containers, target_release, n)
        warnings = _replica_warnings(containers, active.release, n)
        if len(new) < n:
            warnings.append(f"{target_release} has {len(new)} of {n} slot containers")
        start = [c for c in new if c.state != "running"]
        warnings += [f"{c.name} is already running" for c in new if c.state == "running"]
        stop = [
            c for c in containers if _release(c) == active.release and c.state in RESTARTABLE
        ]  # fmt: skip
        effect = (
            f"{service} {active.release} -> {target_release}: start {_names(start)}, wait "
            f"until healthy, then stop {_names(stop)}"
        )
        return self._result(
            would_change=[_change(c, "start") for c in start] + [_change(c, "stop") for c in stop],
            active=active,
            running=_running(containers, active.release),
            shown=old + new,
            service=service,
            slots_available=len(new) == n,
            effect=effect,
            warnings=warnings,
        )

    def _restart(self, service: str) -> dict[str, Any]:
        active = ledger.active(self.ledger_path, service)
        containers = self.containers(service)
        if active.release is None or active.replicas is None:
            targets, warnings, running = containers, [], None
            slots_ok = bool(containers)
        else:
            targets = slots(containers, active.release, active.replicas)
            warnings = _replica_warnings(containers, active.release, active.replicas)
            running = _running(containers, active.release)
            slots_ok = len(targets) == active.replicas
        if not slots_ok:
            warnings.append(f"{service} is missing target containers")
        restart = [c for c in targets if c.state in RESTARTABLE]
        start = [c for c in targets if c.state not in RESTARTABLE]
        parts = [f"restart {_names(restart)} (SIGTERM, 10 s timeout)"] if restart else []
        parts += [f"start {_names(start)}"] if start else []
        return self._result(
            would_change=[_change(c, "restart") for c in restart]
            + [_change(c, "start") for c in start],
            active=active,
            running=running,
            shown=targets,
            service=service,
            slots_available=slots_ok,
            effect=f"{service}: " + ("; ".join(parts) or "nothing to restart"),
            warnings=warnings,
        )

    def _scale(self, service: str, replicas: int) -> dict[str, Any]:
        active = ledger.active(self.ledger_path, service)
        if active.release is None or active.replicas is None:
            raise DryRunUnavailable(f"the ledger has no active release for {service}")
        containers = self.containers(service)
        targets = slots(containers, active.release, max(active.replicas, replicas))
        slots_ok = len(slots(containers, active.release, replicas)) == replicas
        warnings = _replica_warnings(containers, active.release, active.replicas)
        if not slots_ok:
            warnings.append(f"{active.release} has fewer than {replicas} slot containers")
        mine = sorted(
            (c for c in containers if _release(c) == active.release), key=replica_of
        )  # fmt: skip
        start = [c for c in mine if replica_of(c) <= replicas and c.state != "running"]
        stop = [c for c in mine if replica_of(c) > replicas and c.state in RESTARTABLE]
        stray = [c.name for c in stop if c not in targets]
        if stray:
            warnings.append(f"running outside the desired slots: {', '.join(stray)}")
        running = _running(containers, active.release)
        if not start and not stop:
            warnings.append(f"{service} already runs {replicas} replicas")
        effect = f"{service} {active.release}: {running} -> {replicas} running replicas"
        effect += f"; start {_names(start)}" if start else ""
        effect += f"; stop {_names(stop)}" if stop else ""
        return self._result(
            would_change=[_change(c, "start") for c in start] + [_change(c, "stop") for c in stop],
            active=active,
            running=running,
            shown=targets,
            service=service,
            slots_available=slots_ok,
            effect=effect,
            warnings=warnings,
        )

    def _clear_cache(self, cache: str) -> dict[str, Any]:
        warnings: list[str] = []
        keys: int | None
        try:
            keys = cache_keys_of(self.cache_stats(), cache)
        except CacheStatsUnavailable as exc:
            keys = None
            warnings.append(f"cache stats unavailable: {exc}")
        effect = (
            f"delete the {cache} cache"
            + (f" ({keys} keys)" if keys is not None else "")
            + " through cs-api's admin API; it refills on demand"
        )
        return self._result(
            would_change=[{"target": cache, "change": "clear"}],
            active=ledger.Active(None, None),
            running=None,
            shown=[],
            service=CLEAR_CACHE_LIMITS_SERVICE,
            slots_available=True,
            effect=effect,
            warnings=warnings,
            cache_keys=keys,
        )

    # --- shared --------------------------------------------------------------

    def containers(self, service: str) -> list[Container]:
        spec = self.targets.services.get(service)
        if spec is None:  # check 7 refused it already; never list anything else
            raise DryRunUnavailable(f"{service} is not a target")
        listed = self.docker.list_containers(spec.labels)
        return [c for c in listed if self.targets.container_allowed(service, c.name, c.labels)]

    def _preconditions(self, service: str, slots_available: bool) -> dict[str, bool]:
        since = self.now().timestamp() - RATE_WINDOW_SECONDS
        used = self.counters.zcount(rate_key(service), since, float("inf"))
        cooling = self.counters.exists(cooldown_key(service))
        return {
            "rate_limit_ok": used < RATE_LIMIT,
            "cooldown_ok": not cooling,
            "slots_available": slots_available,
        }

    def _result(
        self,
        *,
        would_change: list[dict[str, str]],
        active: ledger.Active,
        running: int | None,
        shown: list[Container],
        service: str,
        slots_available: bool,
        effect: str,
        warnings: list[str],
        cache_keys: int | None = None,
    ) -> dict[str, Any]:
        names = [c.name for c in shown]
        return {
            "would_change": would_change,
            "current": {
                "active_release": active.release,
                "running_replicas": running,
                "containers": [
                    {"name": c.name, "state": c.state, "health": c.health}
                    for c in sorted(shown, key=lambda c: c.name)
                ],
                "cache_keys": cache_keys,
            },
            "preconditions": self._preconditions(service, slots_available),
            "predicted_effect": _clip(effect),
            "warnings": warnings,
            "state_fingerprint": state_fingerprint(active.release, active.replicas, names),
        }


def _release(c: Container) -> str | None:
    return c.labels.get(RELEASE_LABEL)


def replica_of(c: Container) -> int:
    try:
        return int(c.labels.get(REPLICA_LABEL, ""))
    except ValueError:
        return 0  # never inside 1..N, so never a target


def slots(containers: list[Container], release: str, n: int) -> list[Container]:
    """Slots 1..n of a release, in slot order."""
    chosen = [c for c in containers if _release(c) == release and 1 <= replica_of(c) <= n]
    return sorted(chosen, key=replica_of)


def _running(containers: list[Container], release: str) -> int:
    return sum(1 for c in containers if _release(c) == release and c.state == "running")


def _replica_warnings(containers: list[Container], release: str, desired: int) -> list[str]:
    running = _running(containers, release)
    if running == desired:
        return []
    return [f"{running} of {desired} replicas of {release} are running"]


def _change(c: Container, change: str) -> dict[str, str]:
    return {"target": c.name, "change": change}


def _names(containers: list[Container]) -> str:
    return ", ".join(c.name for c in containers) or "nothing"


def _clip(text: str) -> str:
    return text if len(text) <= EFFECT_MAX else text[: EFFECT_MAX - 3] + "..."


def cache_keys_of(stats: dict[str, Any], cache: str) -> int:
    """catalog is one key (present or not); pricing is a hash, counted by entries."""
    try:
        if cache == "catalog":
            return 1 if stats["catalog"]["present"] else 0
        entries = stats["pricing"]["entries"]
    except (KeyError, TypeError) as exc:
        raise CacheStatsUnavailable("unexpected stats shape") from exc
    if not isinstance(entries, int) or isinstance(entries, bool) or entries < 0:
        raise CacheStatsUnavailable("unexpected stats shape")
    return entries
