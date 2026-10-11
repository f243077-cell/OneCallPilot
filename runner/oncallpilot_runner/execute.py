"""Execute handlers (RUN-009 to RUN-013, architecture §5.10). A2.4a: rollback_deploy.

An execute arrives here only after every check of checks.py passed, including
the idempotency claim (SET NX ocp:runner:idem:{execution_id}), so it runs at
most once. Then:

1. **Fingerprint** (RUN-008): the state is read again exactly as the dry run
   read it. A different fingerprint ends the execution `aborted` with
   `STATE_DRIFT`, before anything changes. A state that cannot be read ends it
   `aborted` with `VALIDATION_ERROR` (fail closed).
2. **rollback_deploy** (RUN-011): start slots 1..N of `target_release` (N is the
   ledger's desired count), wait until they pass health (one passing sample,
   up to 60 s), stop the running containers of the active release, append a
   `kind=rollback` record with `deployed_by=runner`.
   **If the new slots never become healthy**, the runner stops the slots it
   started and leaves the old release running: the service never drops to zero
   healthy replicas. The result is `failed`, with no ledger record (ruled
   2026-10-10; contract-review-a issue 14).
3. **Structural health check** of the new slots (health.py).

Each step is reported as a `progress` result (RUN-013); the final
`execution_result` carries `health_after.structural` and
`captured.previous_release` (C4 `captures`). Every Docker write is preceded by a
fresh inspect and the allowlist check (§7.4).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from oncallpilot_runner import ledger
from oncallpilot_runner.docker_client import Container, DockerControl, DockerUnavailable
from oncallpilot_runner.dry_run import RELEASE_LABEL, DryRunner, DryRunUnavailable, slots
from oncallpilot_runner.health import HealthChecker, Sample
from oncallpilot_runner.request import Request
from oncallpilot_runner.targets import Targets

# name, status (running | succeeded | failed), message
Emit = Callable[[str, str, str], None]


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


@dataclass
class Executor:
    docker: DockerControl
    dry_runner: DryRunner
    health: HealthChecker
    targets: Targets
    ledger_path: Path
    now: Callable[[], datetime]
    handlers: dict[str, str] = field(default_factory=lambda: {"rollback_deploy": "_rollback"})

    def execute(self, request: Request, emit: Emit) -> Outcome:
        handler = self.handlers.get(request.action)
        if handler is None:  # A2.4b brings restart, scale and clear_cache
            return Outcome("aborted", "ACTION_NOT_ALLOWED", reason="no execute handler yet")
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
            if c.labels.get(RELEASE_LABEL) == active.release
            and c.state in ("running", "restarting", "paused")
        ]
        started: list[str] = []
        try:
            for name in new:
                if self._start(service, name, emit):
                    started.append(name)
        except (StepFailed, NotAllowed, DockerUnavailable) as exc:
            self._undo(service, started, emit)  # the old release was never touched
            return Outcome("failed", captured=captured, reason=str(exc))
        waiting = f"healthy {', '.join(new)}"[:100]
        emit(waiting, "running", "waiting up to 60 s")
        ready = self.health.wait_healthy(service, new)
        if not ready.passed:
            emit(waiting, "failed", _why(ready))
            self._undo(service, started, emit)
            return Outcome(
                "failed",
                structural=self.health.report(ready, passed=False),
                captured=captured,
                reason=f"{target} did not become healthy; {active.release} kept running",
            )
        emit(waiting, "succeeded", f"{target} passes health")
        try:
            for name in old:
                self._stop(service, name, emit)
            self._record(service, target, n, emit)
        except (StepFailed, NotAllowed, DockerUnavailable) as exc:
            # Both releases may be running now: never stop the healthy new one.
            return Outcome("failed", captured=captured, reason=str(exc))
        emit("structural health check", "running", "up to 60 s, stable for 10 s")
        structural = self.health.structural(service, new)
        if structural["passed"]:
            emit("done", "succeeded", f"{service} runs {target}")
            return Outcome("succeeded", structural=structural, captured=captured)
        emit("done", "failed", "structural health check failed")
        return Outcome("failed", structural=structural, captured=captured)

    # --- steps -------------------------------------------------------------------

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

    def _stop(self, service: str, name: str, emit: Emit) -> None:
        container = self._checked(service, name)
        try:
            self.docker.stop(container.id)
        except DockerUnavailable as exc:
            emit(f"stopping {name}", "failed", str(exc))
            raise StepFailed(f"could not stop {name}") from exc
        emit(f"stopping {name}", "succeeded", "SIGTERM, 10 s timeout")

    def _undo(self, service: str, started: list[str], emit: Emit) -> None:
        """Stop what this execution started; the old release was never touched."""
        for name in started:
            try:
                self._stop(service, name, emit)
            except (StepFailed, NotAllowed, DockerUnavailable):
                pass  # reported by its own progress step; keep stopping the others

    def _record(self, service: str, release: str, replicas: int, emit: Emit) -> None:
        name = "recording the rollback in the deploy ledger"
        try:
            record = ledger.runner_record(
                self.ledger_path, kind="rollback", service=service, release=release,
                replicas=replicas, reason=f"rolled back to release {release}", now=self.now(),
            )  # fmt: skip
            ledger.append(self.ledger_path, record)
        except ledger.LedgerError as exc:
            emit(name, "failed", str(exc)[:300])
            raise StepFailed(str(exc)) from exc
        emit(name, "succeeded", f"kind=rollback, release {release}, replicas {replicas}")


def _why(sample: Sample) -> str:
    parts = [f"{n} missing" for n in sample.missing]
    parts += [f"{c.name} {c.state}/{c.health}" for c in sample.containers
              if not (c.state == "running" and c.health == "healthy")]  # fmt: skip
    parts += [f"{p['url']} {p['error'] or p['status_code']}" for p in sample.probes if not p["ok"]]
    return ("; ".join(parts) or "not healthy")[:300]
