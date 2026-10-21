"""Pure rules of alert ingestion (architecture §5.1, §6.8; FR-004…FR-007).

No I/O here: the router and ``oncallpilot.db.ingest`` do the writing.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from oncallpilot_contracts.domain import Signal
from oncallpilot_contracts.enums import Severity

WINDOW_LEAD = timedelta(minutes=10)

SEV1_ERROR_RATE = 0.25
SEV2_ERROR_RATE = 0.05
SEV2_P95_FACTOR = 3.0


def severity(signals: list[Signal]) -> Severity:
    """FR-006, §6.8.

    ``sev1``: error rate ≥ 25 %. ``sev2``: error rate ≥ 5 %, p95 ≥ 3× its
    baseline, or any restarts signal. Otherwise ``sev3``.

    The spec's other sev1 case, "a service has no running container", cannot
    be read from ``AlertIn`` (a signal has only a value, baseline and z-score),
    so it is not decided here; see handoff.md (open contract question).
    """
    error_rate = max((s.value for s in signals if s.name == "error_rate"), default=0.0)
    if error_rate >= SEV1_ERROR_RATE:
        return "sev1"
    if error_rate >= SEV2_ERROR_RATE:
        return "sev2"
    for s in signals:
        if s.name == "restarts":
            return "sev2"
        if s.name == "p95_latency" and s.baseline and s.value >= SEV2_P95_FACTOR * s.baseline:
            return "sev2"
    return "sev3"


def window_start(signals: list[Signal]) -> datetime:
    """FR-007: the earliest ``first_anomalous_at`` minus 10 minutes."""
    return min(s.first_anomalous_at for s in signals) - WINDOW_LEAD


def describe(signal: Signal) -> str:
    """A short human label for a signal, e.g. ``error rate 31%``."""
    if signal.name == "error_rate":
        return f"error rate {signal.value * 100:.0f}%"
    if signal.name == "job_failure_rate":
        return f"job failure rate {signal.value * 100:.0f}%"
    if signal.name == "p95_latency":
        return f"p95 latency {signal.value * 1000:.0f} ms"
    if signal.name == "restarts":
        return f"{signal.value:.0f} restarts"
    return f"{signal.value:.0f} stack traces/min"


def title(service: str, signals: list[Signal]) -> str:
    """``<service> <first signal>``, e.g. ``api error rate 31%`` (≤ 200 chars)."""
    return f"{service} {describe(signals[0])}"[:200]


def seed_summary(service: str, signals: list[Signal]) -> str:
    labels = ", ".join(describe(s) for s in signals)
    return f"{service} {labels} (detector signal)"[:300]
