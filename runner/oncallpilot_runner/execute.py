"""Execute handlers for the four enabled actions (RUN-009 to RUN-013, architecture §5.10).

An execute arrives here only after every check of checks.py passed, including
the idempotency claim (SET NX ocp:runner:idem:{execution_id}), so it runs at
most once. Then, for every action:

1. **Fingerprint** (RUN-008): the state is read again exactly as the dry run
   read it. A different fingerprint ends the execution `aborted` with
   `STATE_DRIFT`, before anything changes. A state that cannot be read, or target
   slots the dry run already showed missing, end it `aborted` with
   `VALIDATION_ERROR` (fail closed).
2. **The action**, step by step, each step a `progress` result (RUN-013).
   Every Docker write (start, stop, restart) comes right after a fresh inspect
   and both allowlist checks (§7.4), by container ID.
3. **Structural health check** (health.py), whose result goes in
   `health_after.structural`; `captured` holds what C4 `captures` names.

The actions:

- **rollback_deploy** (RUN-011): start slots 1..N of `target_release` (N is the
  ledger's desired count), wait until they pass health (up to 60 s), stop the
  running containers of the active release, append `kind=rollback`. If the new
  slots never get healthy, stop them and keep the old release: `failed`, no
  record (ruled 2026-10-10; contract-review-a issue 14).
- **restart_service** (RUN-009): the active release's slots 1..N (redis: its
  allowlisted container, no ledger values), **one at a time** (ruled 2026-10-11):
  restart a running container (SIGTERM, 10 s timeout) or start an exited one,
  then wait until it passes health before the next. If one never gets healthy,
  stop there: `failed`, the rest untouched. No ledger record (§2.10).
- **scale_service** (RUN-010): `replicas` is a catalogue-bounded integer (1-5),
  so never zero. Start the slots of 1..replicas that are not running, wait until
  all of 1..replicas pass health, then stop the running slots above `replicas`.
  If the started slots never get healthy, stop them again and stop nothing
  else: `failed`, no record. Otherwise append `kind=scale` with the new count;
  `captured.previous_replicas` is the ledger's count before.
- **clear_cache** (RUN-012): read the cache stats (the number of keys that the
  progress step reports, ruled 2026-10-11, because the clear endpoint answers
  only yes or no and C5 has no output field), then
  `POST http://cs-lb/internal/cache/clear` with RUNNER_ADMIN_TOKEN. Structural
  check: the api's active slots and cs-redis. No ledger record.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from oncallpilot_runner import ledger
from oncallpilot_runner.docker_client import Container, DockerControl, DockerUnavailable
from oncallpilot_runner.dry_run import (
    RELEASE_LABEL,
    RESTARTABLE,
    DryRunner,
    DryRunUnavailable,
    cache_keys_of,
    replica_of,
    slots,
)
from oncallpilot_runner.health import HealthChecker, Sample
from oncallpilot_runner.request import Request
from oncallpilot_runner.shop_admin import CacheClearFailed, CacheStatsUnavailable
from oncallpilot_runner.targets import Targets

# name, status (running | succeeded | failed), message
Emit = Callable[[str, str, str], None]
MIN_REPLICAS, MAX_REPLICAS = 1, 5  # C4 scale_service.replicas; checks.py enforces it first


@dataclass
class Outcome:
    """The final answer, before it is signed as a C5 execution_result."""

    status: str  # succeeded | failed | aborted
    error_code: str | None = None
    structural: dict[str, Any] | None = None
    captured: dict[str, str | int] | None = None
    reason: str = ""


class NotAllowed(RuntimeError):
    """A container failed the allowlist on the inspect right before a write."""


class StepFailed(RuntimeError):
    pass


STEP_ERRORS = (StepFailed, NotAllowed, DockerUnavailable)


@dataclass
class Executor:
    docker: DockerControl
    dry_runner: DryRunner
    health: HealthChecker
    targets: Targets
    ledger_path: Path
    now: Callable[[], datetime]
    cache_clear: Callable[[str], bool] | None = None
    handlers: dict[str, str] = field(
        default_factory=lambda: {
            "rollback_deploy": "_rollback",
            "restart_service": "_restart",
            "scale_service": "_scale",
            "clear_cache": "_clear_cache",
        }
    )

    def execute(self, request: Request, emit: Emit) -> Outcome:
        handler = self.handlers.get(request.action)
        if handler is None:
            return Outcome("aborted", "ACTION_NOT_ALLOWED", reason="no execute handler")
        try:
            preview = self.dry_runner.run(request)
        except DryRunUnavailable as exc:
            return Outcome("aborted", "VALIDATION_ERROR", reason=f"state unreadable: {exc}")
        if preview["state_fingerprint"] != request.state_fingerprint:
            return Outcome("aborted", "STATE_DRIFT", reason="the state changed since the dry run")
        if not preview["preconditions"]["slots_available"]:
            return Outcome("aborted", "VALIDATION_ERROR", reason="target slots are missing")
        emit("validated", "succeeded", "signature, catalogue, targets and fingerprint checked")
        result: Outcome = getattr(self, handler)(request, emit)
        return result

    # --- rollback_deploy ---------------------------------------------------------

    def _rollback(self, request: Request, emit: Emit) -> Outcome:
        service, target = str(request.params["service"]), str(request.params["target_release"])
        active = ledger.active(self.ledger_path, service)
        assert active.release is not None and active.replicas is not None  # dry run checked
        captured: dict[str, str | int] = {"previous_release": active.release}
        n = active.replicas
        containers = self.dry_runner.containers(service)
        new = [c.name for c in slots(containers, target, n)]
        old = [
            c.name
            for c in containers
            if c.labels.get(RELEASE_LABEL) == active.release and c.state in RESTARTABLE
        ]
        started: list[str] = []
        try:
            for name in new:
                if self._start(service, name, emit):
                    started.append(name)
        except STEP_ERRORS as exc:
            self._undo(service, started, emit)  # the old release was never touched
            return Outcome("failed", captured=captured, reason=str(exc))
        ready = self._wait_healthy(service, new, emit)
        if not ready.passed:
            self._undo(service, started, emit)
            return Outcome(
                "failed",
                structural=self.health.report(ready, passed=False),
                captured=captured,
                reason=f"{target} did not become healthy; {active.release} kept running",
            )
        try:
            for name in old:
                self._stop(service, name, emit)
            self._record("rollback", service, target, n, f"rolled back to release {target}", emit)
        except STEP_ERRORS as exc:
            # Both releases may be running now: never stop the healthy new one.
            return Outcome("failed", captured=captured, reason=str(exc))
        return self._finish([(service, new)], captured, emit, f"{service} runs {target}")

    # --- restart_service ---------------------------------------------------------

    def _restart(self, request: Request, emit: Emit) -> Outcome:
        service = str(request.params["service"])
        names = self._targets_of(service)
        captured: dict[str, str | int] = {}
        for name in names:
            try:
                self._restart_one(service, name, emit)
            except STEP_ERRORS as exc:
                return Outcome("failed", captured=captured, reason=str(exc))
            ready = self._wait_healthy(service, [name], emit)
            if not ready.passed:  # stop here: the remaining targets stay as they are
                return Outcome(
                    "failed",
                    structural=self.health.report(ready, passed=False),
                    captured=captured,
                    reason=f"{name} did not become healthy after the restart",
                )
        return self._finish([(service, names)], captured, emit, f"{service} restarted")

    # --- scale_service -----------------------------------------------------------

    def _scale(self, request: Request, emit: Emit) -> Outcome:
        service, replicas = str(request.params["service"]), request.params["replicas"]
        if not isinstance(replicas, int) or not MIN_REPLICAS <= replicas <= MAX_REPLICAS:
            return Outcome("aborted", "VALIDATION_ERROR", reason="replicas outside 1-5")
        active = ledger.active(self.ledger_path, service)
        assert active.release is not None and active.replicas is not None  # dry run checked
        captured: dict[str, str | int] = {"previous_replicas": active.replicas}
        containers = self.dry_runner.containers(service)
        keep = [c.name for c in slots(containers, active.release, replicas)]
        extra = [
            c.name
            for c in sorted(containers, key=replica_of)
            if c.labels.get(RELEASE_LABEL) == active.release
            and replica_of(c) > replicas
            and c.state in RESTARTABLE
        ]
        started: list[str] = []
        try:
            for name in keep:
                if self._start(service, name, emit):
                    started.append(name)
        except STEP_ERRORS as exc:
            self._undo(service, started, emit)  # nothing was stopped
            return Outcome("failed", captured=captured, reason=str(exc))
        ready = self._wait_healthy(service, keep, emit)
        if not ready.passed:
            self._undo(service, started, emit)
            return Outcome(
                "failed",
                structural=self.health.report(ready, passed=False),
                captured=captured,
                reason=f"{service} did not reach {replicas} healthy replicas; nothing stopped",
            )
        try:
            for name in reversed(extra):  # the highest slot first
                self._stop(service, name, emit)
            self._record("scale", service, active.release, replicas,
                         f"scaled to {replicas} replicas", emit)  # fmt: skip
        except STEP_ERRORS as exc:
            return Outcome("failed", captured=captured, reason=str(exc))
        return self._finish([(service, keep)], captured, emit, f"{service} runs {replicas}")

    # --- clear_cache -------------------------------------------------------------

    def _clear_cache(self, request: Request, emit: Emit) -> Outcome:
        cache = str(request.params["cache"])
        captured: dict[str, str | int] = {}
        if self.cache_clear is None:
            return Outcome("aborted", "VALIDATION_ERROR", reason="no cache client")
        try:
            keys: int | None = cache_keys_of(self.dry_runner.cache_stats(), cache)
        except CacheStatsUnavailable:
            keys = None
        step = f"clearing the {cache} cache"
        try:
            cleared = self.cache_clear(cache)
        except CacheClearFailed as exc:
            emit(step, "failed", str(exc))
            return Outcome("failed", captured=captured, reason=f"clear failed: {exc}")
        if not cleared:
            message = f"{cache}: already empty, 0 keys cleared"
        elif keys is None:
            message = f"{cache}: cleared (key count unavailable)"
        else:
            message = f"{cache}: {keys} keys cleared"
        emit(step, "succeeded", message)
        groups = [("api", self._targets_of("api")), ("redis", self._targets_of("redis"))]
        return self._finish(groups, captured, emit, message)

    # --- steps -------------------------------------------------------------------

    def _targets_of(self, service: str) -> list[str]:
        """Slots 1..N of the active release; a service without ledger values
        (redis) targets its allowlisted containers."""
        active = ledger.active(self.ledger_path, service)
        containers = self.dry_runner.containers(service)
        if active.release is None or active.replicas is None:
            return sorted(c.name for c in containers)
        return [c.name for c in slots(containers, active.release, active.replicas)]

    def _checked(self, service: str, name: str) -> Container:
        """A fresh inspect, then both allowlist checks, right before a write."""
        container = self.docker.inspect(name)
        if not self.targets.container_allowed(service, container.name, container.labels):
            raise NotAllowed(f"{name} is not an allowed {service} container")
        return container

    def _start(self, service: str, name: str, emit: Emit) -> bool:
        """Starts the container; False if it was already running."""
        container = self._checked(service, name)
        if container.state == "running":
            emit(f"starting {name}", "succeeded", "already running")
            return False
        try:
            self.docker.start(container.id)
        except DockerUnavailable as exc:
            emit(f"starting {name}", "failed", str(exc))
            raise StepFailed(f"could not start {name}") from exc
        emit(f"starting {name}", "succeeded", "")
        return True

    def _restart_one(self, service: str, name: str, emit: Emit) -> None:
        container = self._checked(service, name)
        restart = container.state in RESTARTABLE
        step = f"{'restarting' if restart else 'starting'} {name}"
        try:
            if restart:
                self.docker.restart(container.id)
            else:
                self.docker.start(container.id)
        except DockerUnavailable as exc:
            emit(step, "failed", str(exc))
            raise StepFailed(f"could not {step}") from exc
        emit(step, "succeeded", "SIGTERM, 10 s timeout" if restart else "it had stopped")

    def _stop(self, service: str, name: str, emit: Emit) -> None:
        container = self._checked(service, name)
        try:
            self.docker.stop(container.id)
        except DockerUnavailable as exc:
            emit(f"stopping {name}", "failed", str(exc))
            raise StepFailed(f"could not stop {name}") from exc
        emit(f"stopping {name}", "succeeded", "SIGTERM, 10 s timeout")

    def _undo(self, service: str, started: list[str], emit: Emit) -> None:
        """Stop what this execution started; nothing else was touched yet."""
        for name in started:
            try:
                self._stop(service, name, emit)
            except STEP_ERRORS:
                pass  # reported by its own progress step; keep stopping the others

    def _wait_healthy(self, service: str, names: list[str], emit: Emit) -> Sample:
        step = f"healthy {', '.join(names)}"[:100]
        emit(step, "running", "waiting up to 60 s")
        ready = self.health.wait_healthy(service, names)
        emit(step, "succeeded" if ready.passed else "failed",
             "passes health" if ready.passed else _why(ready))  # fmt: skip
        return ready

    def _record(
        self, kind: str, service: str, release: str, replicas: int, reason: str, emit: Emit
    ) -> None:
        name = f"recording the {kind} in the deploy ledger"
        try:
            record = ledger.runner_record(
                self.ledger_path, kind=kind, service=service, release=release,
                replicas=replicas, reason=reason, now=self.now(),
            )  # fmt: skip
            ledger.append(self.ledger_path, record)
        except ledger.LedgerError as exc:
            emit(name, "failed", str(exc)[:300])
            raise StepFailed(str(exc)) from exc
        emit(name, "succeeded", f"kind={kind}, release {release}, replicas {replicas}")

    def _finish(
        self, groups: list[tuple[str, list[str]]], captured: dict[str, str | int], emit: Emit,
        done: str,
    ) -> Outcome:  # fmt: skip
        emit("structural health check", "running", "up to 60 s, stable for 10 s")
        structural = self.health.structural_groups(groups)
        if structural["passed"]:
            emit("done", "succeeded", done[:300])
            return Outcome("succeeded", structural=structural, captured=captured)
        emit("done", "failed", "structural health check failed")
        return Outcome("failed", structural=structural, captured=captured)


def _why(sample: Sample) -> str:
    parts = [f"{n} missing" for n in sample.missing]
    parts += [f"{c.name} {c.state}/{c.health}" for c in sample.containers
              if not (c.state == "running" and c.health == "healthy")]  # fmt: skip
    parts += [f"{p['url']} {p['error'] or p['status_code']}" for p in sample.probes if not p["ok"]]
    return ("; ".join(parts) or "not healthy")[:300]
