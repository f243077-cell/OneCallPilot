"""``get_service_health`` (architecture §6.3; AI-008): container state through socket-proxy-ro.

Only ``GET`` requests to the read-only proxy, and only containers labelled
``oncallpilot.managed=true`` and ``oncallpilot.service=<service>``. Each
container is **projected** to the fields of ``ContainerHealth``: environment
variables, mounts, commands and labels outside ``oncallpilot.*`` are never
read into the result (``CLAUDE.md`` §5.1). Then one HTTP ``/healthz`` probe
of the service on chaos_net, where it has one.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any

import httpx
from oncallpilot_contracts.domain import ContainerHealth, ProbeResult, ServiceHealthPayload

from oncallpilot.agent.tools.params import GetServiceHealthParams

DOCKER_TIMEOUT_SECONDS = 5.0
PROBE_TIMEOUT_SECONDS = 2.0
PROBES: dict[str, str] = {
    "api": "http://api-upstream:8000/healthz",
    "worker": "http://worker-upstream:8001/healthz",
    "payments": "http://cs-payments:8002/healthz",
    "lb": "http://cs-lb/healthz",
}
_STATES = {"created", "restarting", "running", "removing", "paused", "exited", "dead"}
_HEALTH = {"healthy", "unhealthy", "starting"}


def docker_base_url(docker_host: str) -> str:
    """``tcp://socket-proxy-ro:2375`` → ``http://socket-proxy-ro:2375`` (TCP only)."""
    if not docker_host.startswith("tcp://"):
        raise ValueError("DOCKER_HOST must be tcp://socket-proxy-ro:2375 (never a socket)")
    return "http://" + docker_host.removeprefix("tcp://").rstrip("/")


def docker_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or value.startswith("0001-"):
        return None
    text = value.rstrip("Z")
    head, _, frac = text.partition(".")
    return datetime.fromisoformat(f"{head}.{(frac or '0')[:6]}+00:00")


def project(inspect: dict[str, Any]) -> ContainerHealth:
    """The allowed fields of one ``GET /containers/{id}/json`` answer, nothing else."""
    state = inspect.get("State") or {}
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    status = state.get("Status") if state.get("Status") in _STATES else "dead"
    health = (state.get("Health") or {}).get("Status")
    running = status == "running"
    return ContainerHealth(
        name=str(inspect.get("Name", "")).lstrip("/"),
        release=labels.get("oncallpilot.release", "0.0"),
        state=status,
        health=health if health in _HEALTH else "none",
        restart_count=int(inspect.get("RestartCount") or 0),
        oom_killed=bool(state.get("OOMKilled")),
        exit_code=None if running else state.get("ExitCode"),
        started_at=docker_time(state.get("StartedAt")),
    )


async def _probe(http: httpx.AsyncClient, url: str) -> ProbeResult:
    started = time.monotonic()
    try:
        response = await http.get(url, timeout=PROBE_TIMEOUT_SECONDS)
        return ProbeResult(
            url=url,
            ok=response.status_code == 200,
            status_code=response.status_code,
            latency_ms=round((time.monotonic() - started) * 1000, 1),
            error=None,
        )
    except httpx.HTTPError as exc:
        return ProbeResult(
            url=url, ok=False, status_code=None, latency_ms=None, error=type(exc).__name__
        )


async def get_service_health(
    docker: httpx.AsyncClient, http: httpx.AsyncClient, params: GetServiceHealthParams
) -> ServiceHealthPayload:
    """``docker`` has the proxy as its base URL; ``http`` reaches chaos_net."""
    filters = json.dumps(
        {"label": ["oncallpilot.managed=true", f"oncallpilot.service={params.service}"]}
    )
    listing = await docker.get(
        "/containers/json", params={"all": "1", "filters": filters}, timeout=DOCKER_TIMEOUT_SECONDS
    )
    listing.raise_for_status()
    containers = []
    for item in sorted(listing.json(), key=lambda c: (c.get("Names") or [""])[0]):
        detail = await docker.get(f"/containers/{item['Id']}/json", timeout=DOCKER_TIMEOUT_SECONDS)
        detail.raise_for_status()
        containers.append(project(detail.json()))
    url = PROBES.get(params.service)
    probe = await _probe(http, url) if url else None
    return ServiceHealthPayload(service=params.service, containers=containers, probe=probe)
