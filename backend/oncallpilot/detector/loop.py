"""The detector's polling loop (FR-001, FR-003; architecture §5.1, §6.8).

Every ``DETECTOR_INTERVAL_SECONDS`` (5 s): look for a new ``kind=reset``
record in the deploy ledger (→ warm-up), collect one ``Samples`` per
monitored service, step the engine, and send each alert to
``POST /ingest/alert``. A failing source or a failed send is logged and the
cycle goes on; the loop itself never stops on an error (FR-001). An alert
that could not be delivered is retried every cycle until it is (the engine
fires a signal only once, and the API deduplicates per service).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import asyncpg
import httpx
from oncallpilot_contracts.api import AlertIn
from pydantic import SecretStr, ValidationError

from oncallpilot.detector.config import load_config
from oncallpilot.detector.engine import Alert, Cycle, Detector, Samples

log = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 5.0


class SampleSource(Protocol):
    """Observations of every monitored service at ``at`` (Prometheus, Loki, socket-proxy-ro)."""

    async def collect(self, at: datetime) -> dict[str, Samples]: ...


class AlertSink(Protocol):
    async def send(self, alert: AlertIn) -> None: ...


ResetProbe = Callable[[], Awaitable[tuple[UUID, datetime] | None]]
"""Returns the newest ``kind=reset`` record's id and time, if any."""


@dataclass(frozen=True)
class MonitorSettingsView:
    """What the detector takes from ``monitor_settings`` (FR-023, ADR-20)."""

    version: int
    services: frozenset[str] | None  # None: every service in the rules
    thresholds: dict[str, float]


SettingsProbe = Callable[[], Awaitable[MonitorSettingsView | None]]


class HttpAlertSink:
    """``POST /ingest/alert`` with the ingest token (API-003)."""

    def __init__(self, client: httpx.AsyncClient, url: str, token: SecretStr) -> None:
        self._client = client
        self._url = url
        self._token = token

    async def send(self, alert: AlertIn) -> None:
        response = await self._client.post(
            self._url,
            content=alert.model_dump_json(),
            headers={
                "Content-Type": "application/json",
                "X-Ingest-Token": self._token.get_secret_value(),
            },
            timeout=SEND_TIMEOUT_SECONDS,
        )
        response.raise_for_status()


class DetectorLoop:
    def __init__(
        self,
        detector: Detector,
        source: SampleSource,
        sink: AlertSink,
        resets: ResetProbe,
        on_recovered: Callable[[str, datetime], Awaitable[None]] | None = None,
        on_samples: Callable[[datetime, dict[str, Samples]], None] | None = None,
        settings: SettingsProbe | None = None,
        base_config: bytes | None = None,
    ) -> None:
        self.detector = detector
        self.source = source
        self.sink = sink
        self.resets = resets
        self.on_recovered = on_recovered
        self.on_samples = on_samples  # the signal verifier reads the latest samples
        self._last_reset: UUID | None = None
        self._first = True
        self._undelivered: list[AlertIn] = []
        self.settings = settings
        self.base_config = base_config
        self._settings_version: int | None = None
        self._services: frozenset[str] | None = None

    async def _refresh_settings(self) -> None:
        """Apply new thresholds and monitored services; keep the old ones on any error."""
        if self.settings is None or self.base_config is None:
            return
        try:
            view = await self.settings()
        except (OSError, asyncpg.PostgresError) as exc:
            log.warning("monitor settings unreadable: %s", type(exc).__name__)
            return
        if view is None or view.version == self._settings_version:
            return
        try:
            self.detector.config = load_config(self.base_config, view.thresholds)
        except (ValueError, ValidationError) as exc:
            log.error("monitor settings version %d ignored: %s", view.version, exc)
            self._settings_version = view.version
            return
        self._services = view.services
        self._settings_version = view.version
        log.info("detector uses monitor settings version %d", view.version)

    async def cycle(self, at: datetime) -> Cycle:
        await self._refresh_settings()
        await self._check_reset(at)
        try:
            samples = await self.source.collect(at)
        except Exception as exc:  # FR-001: whatever a source raises skips one cycle, logged
            log.warning("detector samples failed: %s", type(exc).__name__)
            await self._deliver()
            return Cycle(alerts=[], recovered=[])
        if self.on_samples is not None:
            self.on_samples(at, samples)
        if self._services is not None:
            samples = {k: v for k, v in samples.items() if k in self._services}
        result = self.detector.step(at, samples)
        self._undelivered += [self._body(alert) for alert in result.alerts]
        await self._deliver()
        for service in result.recovered:
            log.info("service recovered: %s", service)
            if self.on_recovered is not None:
                await self.on_recovered(service, at)
        return result

    async def run(
        self,
        interval: float,
        now: Callable[[], datetime],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        cycles: int | None = None,
    ) -> None:
        """Run until cancelled (or for ``cycles`` cycles, in tests)."""
        done = 0
        while cycles is None or done < cycles:
            started = now()
            await self.cycle(started)
            done += 1
            elapsed = (now() - started).total_seconds()
            await sleep(max(0.0, interval - elapsed))

    async def _check_reset(self, at: datetime) -> None:
        try:
            latest = await self.resets()
        except OSError as exc:
            log.warning("deploy ledger unreadable: %s", type(exc).__name__)
            return
        reset_id = latest[0] if latest else None
        if self._first:
            # The reset that is already there at start-up does not restart the warm-up.
            self._first = False
            self._last_reset = reset_id
            return
        if reset_id is not None and reset_id != self._last_reset:
            self._last_reset = reset_id
            log.info("chaos reset seen in the deploy ledger: warm-up starts")
            self.detector.reset(at)

    @staticmethod
    def _body(alert: Alert) -> AlertIn:
        return AlertIn.model_validate(
            {
                "source": "detector",
                "service": alert.service,
                "signals": alert.signals,
                "observed_at": alert.observed_at,
            }
        )

    @property
    def undelivered(self) -> int:
        return len(self._undelivered)

    async def _deliver(self) -> None:
        waiting, self._undelivered = self._undelivered, []
        for body in waiting:
            try:
                await self.sink.send(body)
                log.info("alert sent for %s: %s", body.service, [s.name for s in body.signals])
            except (httpx.HTTPError, OSError) as exc:
                log.error("alert for %s not delivered: %s", body.service, type(exc).__name__)
                self._undelivered.append(body)
