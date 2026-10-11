"""The structural health check (RUN-014, architecture §5.10 step 4, C1 StructuralHealth).

A sample passes when every target container exists, passes the allowlist, is
`running` with Docker health `healthy`, and every probe answers 200. Probes
(ruled 2026-10-10): each target's own `/healthz` on chaos_net (api :8000,
worker :8001), plus `http://cs-lb/healthz` for api. cs-lb answers that path
itself, so on its own it would prove nothing about the api release.

The check passes when a passing sample is followed, 10 s later, by a second
passing sample with the same restart counts (ruled 2026-10-10), all within 60 s.
Otherwise it fails and reports its last sample. `wait_healthy` is the shorter
wait inside a rollback (one passing sample) before the old release is stopped.
"""

import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from oncallpilot_runner.docker_client import Container, DockerReader, DockerUnavailable
from oncallpilot_runner.results import timestamp
from oncallpilot_runner.targets import Targets

CHECK_SECONDS = 60.0
STABLE_SECONDS = 10.0
POLL_SECONDS = 2.0
PROBE_TIMEOUT_SECONDS = 3.0
PROBE_PORTS = {"api": 8000, "worker": 8001}
LB_PROBE = "http://cs-lb/healthz"

Probe = Callable[[str], dict[str, Any]]


def http_probe(url: str) -> dict[str, Any]:
    """C1 ProbeResult for one GET."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT_SECONDS) as response:
            status: int | None = response.status
            error = None
    except urllib.error.HTTPError as exc:
        status, error = exc.code, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        status, error = None, type(exc).__name__
    latency = round((time.monotonic() - started) * 1000, 1)
    return {"url": url, "ok": status == 200, "status_code": status,
            "latency_ms": latency if status is not None else None, "error": error}  # fmt: skip


@dataclass
class Sample:
    containers: list[Container]
    probes: list[dict[str, Any]]
    missing: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            not self.missing
            and all(c.state == "running" and c.health == "healthy" for c in self.containers)
            and all(p["ok"] for p in self.probes)
        )

    def restarts(self) -> dict[str, int]:
        return {c.name: c.restart_count for c in self.containers}


@dataclass
class HealthChecker:
    docker: DockerReader
    targets: Targets
    probe: Probe
    now: Callable[[], datetime]
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    tick: Callable[[], None] = lambda: None  # keeps the runner's heartbeat fresh

    def sample(self, service: str, names: list[str]) -> Sample:
        spec = self.targets.services[service]
        try:
            listed = self.docker.list_containers(spec.labels)
        except DockerUnavailable:
            return Sample([], [], missing=list(names))
        allowed = {
            c.name: c for c in listed if self.targets.container_allowed(service, c.name, c.labels)
        }  # fmt: skip
        found = [allowed[n] for n in names if n in allowed]
        probes = [self.probe(url) for url in probe_urls(service, [c.name for c in found])]
        return Sample(found, probes, missing=[n for n in names if n not in allowed])

    def wait_healthy(self, service: str, names: list[str]) -> Sample:
        """Polls until one sample passes, or CHECK_SECONDS pass."""
        deadline = self.monotonic() + CHECK_SECONDS
        while True:
            current = self.sample(service, names)
            if current.passed or self.monotonic() >= deadline:
                return current
            self._pause()

    def structural(self, service: str, names: list[str]) -> dict[str, Any]:
        """C1 StructuralHealth for these target containers."""
        deadline = self.monotonic() + CHECK_SECONDS
        while True:
            first = self.sample(service, names)
            if first.passed and self.monotonic() + STABLE_SECONDS <= deadline:
                self._wait(STABLE_SECONDS)
                second = self.sample(service, names)
                if second.passed and second.restarts() == first.restarts():
                    return self.report(second, passed=True)
                first = second
            if self.monotonic() >= deadline:
                return self.report(first, passed=False)
            self._pause()

    def _pause(self) -> None:
        self._wait(POLL_SECONDS)

    def _wait(self, seconds: float) -> None:
        self.tick()
        self.sleep(seconds)

    def report(self, sample: Sample, *, passed: bool) -> dict[str, Any]:
        return {
            "passed": passed and sample.passed,
            "checked_at": timestamp(self.now()),
            "containers": [
                {"name": c.name, "state": c.state, "health": c.health}
                for c in sorted(sample.containers, key=lambda c: c.name)
            ],
            "probes": sample.probes,
        }


def probe_urls(service: str, names: list[str]) -> list[str]:
    port = PROBE_PORTS.get(service)
    urls = [f"http://{name}:{port}/healthz" for name in sorted(names)] if port else []
    return urls + ([LB_PROBE] if service == "api" else [])
