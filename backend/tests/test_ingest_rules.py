"""Severity, window and titles of alert ingestion (FR-006, FR-007)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from oncallpilot_contracts.domain import Signal

from oncallpilot.ingest import describe, seed_summary, severity, title, window_start

T = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)


def sig(name: str, value: float, baseline: float | None = None, at: datetime = T) -> Signal:
    return Signal(name=name, value=value, baseline=baseline, zscore=5.0, first_anomalous_at=at)


@pytest.mark.parametrize(
    ("signals", "expected"),
    [
        ([sig("error_rate", 0.25)], "sev1"),
        ([sig("error_rate", 0.31)], "sev1"),
        ([sig("error_rate", 0.249)], "sev2"),
        ([sig("error_rate", 0.05)], "sev2"),
        ([sig("error_rate", 0.0499)], "sev3"),
        ([sig("p95_latency", 0.9, baseline=0.3)], "sev2"),
        ([sig("p95_latency", 0.899, baseline=0.3)], "sev3"),
        ([sig("p95_latency", 5.0, baseline=None)], "sev3"),
        ([sig("restarts", 2)], "sev2"),
        ([sig("job_failure_rate", 0.4)], "sev3"),
        ([sig("stack_traces", 12)], "sev3"),
        ([sig("stack_traces", 12), sig("error_rate", 0.3)], "sev1"),
        ([sig("restarts", 3), sig("error_rate", 0.01)], "sev2"),
    ],
)
def test_severity(signals: list[Signal], expected: str) -> None:
    assert severity(signals) == expected


def test_window_starts_ten_minutes_before_the_earliest_anomaly() -> None:
    signals = [sig("error_rate", 0.3, at=T), sig("stack_traces", 9, at=T - timedelta(seconds=40))]
    assert window_start(signals) == T - timedelta(minutes=10, seconds=40)


def test_titles_and_summaries() -> None:
    assert title("api", [sig("error_rate", 0.31)]) == "api error rate 31%"
    assert describe(sig("p95_latency", 1.234)) == "p95 latency 1234 ms"
    assert describe(sig("restarts", 3)) == "3 restarts"
    assert describe(sig("job_failure_rate", 0.125)) == "job failure rate 12%"
    assert describe(sig("stack_traces", 7)) == "7 stack traces/min"
    assert seed_summary("api", [sig("error_rate", 0.31)]) == "api error rate 31% (detector signal)"
