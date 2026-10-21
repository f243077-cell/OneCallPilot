"""The detector's decision rules (architecture §6.8; FR-002, FR-003, FR-011).

Pure and deterministic: the polling loop (``oncallpilot.detector.loop``) feeds
one ``Samples`` per service and cycle, with the time passed in, and gets back
the alerts to send and the services that recovered. No I/O, no clock.

- **Statistical signals** (``error_rate``, ``p95_latency``,
  ``job_failure_rate``): an EWMA (α from the config) of mean and variance.
  A sample *breaches* when its z-score against the current baseline is at
  least ``z`` and it clears the absolute floors. The signal triggers after
  ``consecutive`` breaching samples in a row. While a signal is breaching or
  triggered, its mean and variance are frozen, so the fault does not become
  the new normal.
- **restarts**: ≥ ``min_restarts`` in the window, a container that exited
  with a non-zero code, or no running container for a service that should
  have one. Triggers at once.
- **stack_traces**: ≥ ``min_per_minute`` tracebacks with an exception type
  not seen during the warm-up.
- **Warm-up**: for ``warmup_seconds`` after start or a reset nothing
  triggers (FR-003). Baselines keep learning from normal samples during it,
  while breaching samples stay frozen out as always, so a fault injected right
  after ``chaos reset`` is not learnt as normal: it triggers as soon as the
  warm-up ends. (Relearning from scratch would make that impossible.)
- **Recovery**: a service whose triggered signals have all been normal for
  ``recovery_minutes`` is reported once as recovered (FR-011).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from oncallpilot_contracts.domain import Signal
from oncallpilot_contracts.enums import SignalName

from oncallpilot.detector.config import DetectorConfig, StatisticalRule

STATISTICAL: tuple[SignalName, ...] = ("error_rate", "p95_latency", "job_failure_rate")


@dataclass
class Ewma:
    """Exponentially weighted mean and variance (West's incremental form)."""

    alpha: float
    mean: float | None = None
    var: float = 0.0
    samples: int = 0

    def zscore(self, value: float) -> float | None:
        """z of ``value`` against the current baseline; None until there is one."""
        if self.mean is None or self.samples < 2:
            return None
        if self.var <= 0.0:
            return 0.0 if value == self.mean else math.copysign(math.inf, value - self.mean)
        return (value - self.mean) / math.sqrt(self.var)

    def update(self, value: float) -> None:
        if self.mean is None:
            self.mean = value
        else:
            diff = value - self.mean
            increment = self.alpha * diff
            self.mean += increment
            self.var = (1 - self.alpha) * (self.var + diff * increment)
        self.samples += 1


@dataclass(frozen=True)
class RestartsSample:
    restarts_in_window: int
    exited_nonzero: bool
    running: int
    expected_running: bool = True


@dataclass(frozen=True)
class TracesSample:
    per_minute: int
    exception_types: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Samples:
    """One cycle's observations for one service; a missing value is skipped."""

    error_rate: float | None = None
    p95_latency: float | None = None
    job_failure_rate: float | None = None
    restarts: RestartsSample | None = None
    stack_traces: TracesSample | None = None


@dataclass
class _SignalState:
    ewma: Ewma
    streak: int = 0
    streak_started: datetime | None = None
    triggered: bool = False
    normal_since: datetime | None = None


@dataclass
class _ServiceState:
    signals: dict[SignalName, _SignalState] = field(default_factory=dict)
    warmup_exceptions: set[str] = field(default_factory=set)
    open: bool = False  # an alert went out and recovery has not been reported yet


@dataclass(frozen=True)
class Alert:
    service: str
    signals: list[Signal]
    observed_at: datetime


@dataclass(frozen=True)
class Cycle:
    alerts: list[Alert]
    recovered: list[str]


class Detector:
    def __init__(self, config: DetectorConfig, started_at: datetime) -> None:
        self.config = config
        self._services: dict[str, _ServiceState] = {}
        self._warm_until = started_at + timedelta(seconds=config.warmup_seconds)

    # --- public -------------------------------------------------------------

    def reset(self, at: datetime) -> None:
        """A ``kind=reset`` ledger record: warm up again (FR-003).

        Exception types seen during the new warm-up become the known ones.
        """
        for state in self._services.values():
            state.warmup_exceptions.clear()
        self._warm_until = at + timedelta(seconds=self.config.warmup_seconds)

    def warming_up(self, at: datetime) -> bool:
        return at < self._warm_until

    def step(self, at: datetime, samples: dict[str, Samples]) -> Cycle:
        alerts: list[Alert] = []
        recovered: list[str] = []
        for service, sample in samples.items():
            state = self._services.setdefault(service, _ServiceState())
            fired = self._evaluate(service, state, sample, at)
            if fired:
                state.open = True
                alerts.append(Alert(service=service, signals=fired, observed_at=at))
            elif state.open and self._recovered(state, at):
                state.open = False
                for signal in state.signals.values():
                    signal.triggered = False
                    signal.normal_since = None
                recovered.append(service)
        return Cycle(alerts=alerts, recovered=recovered)

    # --- rules --------------------------------------------------------------

    def _evaluate(
        self, service: str, state: _ServiceState, sample: Samples, at: datetime
    ) -> list[Signal]:
        warming = self.warming_up(at)
        fired: list[Signal] = []
        rules = self.config.signals
        for name in STATISTICAL:
            rule: StatisticalRule = getattr(rules, name)
            value: float | None = getattr(sample, name)
            if value is None or service not in rule.services:
                continue
            signal = self._statistical(state, name, rule, value, at, warming)
            if signal is not None:
                fired.append(signal)
        if sample.restarts is not None and service in rules.restarts.services:
            signal = self._restarts(state, sample.restarts, at, warming)
            if signal is not None:
                fired.append(signal)
        if sample.stack_traces is not None and service in rules.stack_traces.services:
            signal = self._traces(state, sample.stack_traces, at, warming)
            if signal is not None:
                fired.append(signal)
        return fired

    def _state(self, state: _ServiceState, name: SignalName) -> _SignalState:
        if name not in state.signals:
            state.signals[name] = _SignalState(Ewma(self.config.ewma_alpha))
        return state.signals[name]

    def _statistical(
        self,
        service_state: _ServiceState,
        name: SignalName,
        rule: StatisticalRule,
        value: float,
        at: datetime,
        warming: bool,
    ) -> Signal | None:
        s = self._state(service_state, name)
        z = s.ewma.zscore(value)
        baseline = s.ewma.mean
        breach = (
            z is not None
            and z >= rule.z
            and value >= rule.min_value
            and value >= rule.min_seconds
            and (baseline is None or value >= rule.min_factor * baseline)
        )
        if not breach:
            s.streak, s.streak_started = 0, None
            self._mark_normal(s, at)
            if not s.triggered:
                s.ewma.update(value)  # frozen while triggered
            return None
        # Breaching: the baseline stays frozen.
        s.normal_since = None
        s.streak += 1
        if s.streak_started is None:
            s.streak_started = at
        if warming or s.triggered or s.streak < rule.consecutive:
            return None
        s.triggered = True
        return Signal(
            name=name,
            value=value,
            baseline=baseline,
            zscore=None if z is None or math.isinf(z) else round(z, 2),
            first_anomalous_at=s.streak_started,
        )

    def _restarts(
        self, service_state: _ServiceState, sample: RestartsSample, at: datetime, warming: bool
    ) -> Signal | None:
        rule = self.config.signals.restarts
        s = self._state(service_state, "restarts")
        breach = (
            sample.restarts_in_window >= rule.min_restarts
            or sample.exited_nonzero
            or (sample.expected_running and sample.running == 0)
        )
        if warming or not breach:
            self._mark_normal(s, at)
            return None
        s.normal_since = None
        if s.triggered:
            return None
        s.triggered = True
        return Signal(
            name="restarts",
            value=float(sample.restarts_in_window),
            baseline=None,
            zscore=None,
            first_anomalous_at=at,
        )

    def _traces(
        self, service_state: _ServiceState, sample: TracesSample, at: datetime, warming: bool
    ) -> Signal | None:
        rule = self.config.signals.stack_traces
        s = self._state(service_state, "stack_traces")
        if warming:
            service_state.warmup_exceptions |= sample.exception_types
            return None
        new_types = sample.exception_types - service_state.warmup_exceptions
        breach = sample.per_minute >= rule.min_per_minute and bool(new_types)
        if not breach:
            self._mark_normal(s, at)
            return None
        s.normal_since = None
        if s.triggered:
            return None
        s.triggered = True
        return Signal(
            name="stack_traces",
            value=float(sample.per_minute),
            baseline=None,
            zscore=None,
            first_anomalous_at=at,
        )

    # --- recovery -----------------------------------------------------------

    @staticmethod
    def _mark_normal(s: _SignalState, at: datetime) -> None:
        if s.triggered and s.normal_since is None:
            s.normal_since = at

    def _recovered(self, state: _ServiceState, at: datetime) -> bool:
        needed = timedelta(minutes=self.config.recovery_minutes)
        triggered = [s for s in state.signals.values() if s.triggered]
        return bool(triggered) and all(
            s.normal_since is not None and at - s.normal_since >= needed for s in triggered
        )
