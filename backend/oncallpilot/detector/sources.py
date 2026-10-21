"""The detector's live sample source (architecture §6.8; FR-001, FR-002).

One ``Samples`` per monitored service per cycle, read-only:

- ``error_rate``, ``p95_latency``, ``job_failure_rate``: Prometheus instant
  queries with the same fixed PromQL as ``query_metrics``;
- ``restarts``: socket-proxy-ro (GET only), containers labelled
  ``oncallpilot.managed=true``: restarts of running or restarting containers
  within the rule's window (from ``RestartCount`` seen over time), a
  container that exited non-zero within the window, and the number running;
- ``stack_traces``: Loki, lines containing ``Traceback`` in the last minute,
  by ``exc_type``.

A value that cannot be read is left out (the engine skips it) and logged; a
cycle never fails because one source is down.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from oncallpilot.agent.tools.clients import LokiClient, PrometheusClient, QueryError
from oncallpilot.agent.tools.get_service_health import DOCKER_TIMEOUT_SECONDS, docker_time
from oncallpilot.agent.tools.promql import render
from oncallpilot.detector.config import DetectorConfig
from oncallpilot.detector.engine import RestartsSample, Samples, TracesSample

log = logging.getLogger(__name__)

TRACES_LOGQL = (
    'sum by (exc_type) (count_over_time({{service="{s}"}} |= "Traceback" | json exc_type [1m]))'
)
MANAGED = json.dumps({"label": ["oncallpilot.managed=true"]})


@dataclass(frozen=True)
class ContainerView:
    name: str
    service: str
    running: bool
    active: bool  # running or restarting: not stopped on purpose
    restart_count: int
    exit_code: int | None
    finished_at: datetime | None


@dataclass
class RestartHistory:
    """``RestartCount`` per container over time, to count restarts within a window."""

    window: timedelta
    _seen: dict[str, deque[tuple[datetime, int]]] = field(
        default_factory=lambda: defaultdict(deque)
    )

    def restarts(self, name: str, count: int, at: datetime) -> int:
        seen = self._seen[name]
        if seen and count < seen[-1][1]:
            seen.clear()  # the container was recreated
        seen.append((at, count))
        while len(seen) > 1 and seen[1][0] <= at - self.window:
            seen.popleft()
        return max(0, count - seen[0][1])


def _view(inspect: dict[str, Any]) -> ContainerView:
    state = inspect.get("State") or {}
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    running = state.get("Status") == "running"
    return ContainerView(
        name=str(inspect.get("Name", "")).lstrip("/"),
        service=str(labels.get("oncallpilot.service", "")),
        running=running,
        active=state.get("Status") in ("running", "restarting"),
        restart_count=int(inspect.get("RestartCount") or 0),
        exit_code=None if running else int(state.get("ExitCode") or 0),
        finished_at=docker_time(state.get("FinishedAt")),
    )


@dataclass
class LiveSampleSource:
    config: DetectorConfig
    prometheus: PrometheusClient
    loki: LokiClient
    docker: httpx.AsyncClient  # base URL = socket-proxy-ro
    history: RestartHistory | None = None

    def __post_init__(self) -> None:
        if self.history is None:
            window = timedelta(seconds=self.config.signals.restarts.window_seconds)
            self.history = RestartHistory(window)

    async def collect(self, at: datetime) -> dict[str, Samples]:
        rules = self.config.signals
        services = sorted(
            {
                *rules.error_rate.services,
                *rules.p95_latency.services,
                *rules.job_failure_rate.services,
                *rules.restarts.services,
                *rules.stack_traces.services,
            }
        )
        containers = await self._containers()
        out: dict[str, Samples] = {}
        for service in services:
            out[service] = Samples(
                error_rate=await self._metric("error_rate", service, at)
                if service in rules.error_rate.services
                else None,
                p95_latency=await self._metric("p95_latency", service, at)
                if service in rules.p95_latency.services
                else None,
                job_failure_rate=await self._metric("job_failure_rate", service, at)
                if service in rules.job_failure_rate.services
                else None,
                restarts=self._restarts(service, containers, at)
                if service in rules.restarts.services
                else None,
                stack_traces=await self._traces(service, at)
                if service in rules.stack_traces.services
                else None,
            )
        return out

    async def _metric(self, template: str, service: str, at: datetime) -> float | None:
        try:
            return await self.prometheus.query_value(render(template, service), at)
        except QueryError as exc:
            log.warning("prometheus %s for %s unavailable: %s", template, service, exc)
            return None

    async def _traces(self, service: str, at: datetime) -> TracesSample | None:
        try:
            series = await self.loki.query_vector(TRACES_LOGQL.format(s=service), at)
        except QueryError as exc:
            log.warning("loki stack traces for %s unavailable: %s", service, exc)
            return None
        per_minute = int(sum(value for _, value in series))
        types = frozenset(labels.get("exc_type", "") for labels, _ in series) - {""}
        return TracesSample(per_minute=per_minute, exception_types=types)

    async def _containers(self) -> list[ContainerView] | None:
        try:
            listing = await self.docker.get(
                "/containers/json",
                params={"all": "1", "filters": MANAGED},
                timeout=DOCKER_TIMEOUT_SECONDS,
            )
            listing.raise_for_status()
            views = []
            for item in listing.json():
                detail = await self.docker.get(
                    f"/containers/{item['Id']}/json", timeout=DOCKER_TIMEOUT_SECONDS
                )
                detail.raise_for_status()
                views.append(_view(detail.json()))
            return views
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            log.warning("container states unavailable: %s", type(exc).__name__)
            return None

    def _restarts(
        self, service: str, containers: list[ContainerView] | None, at: datetime
    ) -> RestartsSample | None:
        if containers is None:
            return None
        assert self.history is not None
        mine = [c for c in containers if c.service == service]
        window_start = at - self.history.window
        # A slot stopped on purpose (an older release after a rollback) no longer counts.
        restarts = sum(self.history.restarts(c.name, c.restart_count, at) for c in mine if c.active)
        return RestartsSample(
            restarts_in_window=restarts,
            exited_nonzero=any(
                not c.running
                and c.exit_code not in (None, 0)
                and c.finished_at is not None
                and c.finished_at >= window_start
                for c in mine
            ),
            running=sum(1 for c in mine if c.running),
        )
