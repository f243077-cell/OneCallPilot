"""The time window a tool may read: the incident window ± 30 minutes (§6.3, AI-005),
and never earlier than the settled end of the last testbed reset (ADR-27)."""

from __future__ import annotations

from datetime import datetime, timedelta

MARGIN = timedelta(minutes=30)
# ADR-27: `chaos reset` writes its ledger record before it restores the testbed (up to
# 90 s, TB-007) and 1-minute query windows show the old fault 60 s longer: the same
# 150 s the detector settles for (ADR-24, `warmup_settle_seconds`).
RESET_SETTLE = timedelta(seconds=150)


class WindowError(ValueError):
    """The requested window is empty after clamping."""


def clamp(
    requested_start: datetime | None,
    requested_end: datetime | None,
    incident_start: datetime,
    incident_end: datetime | None,
    now: datetime,
    not_before: datetime | None = None,
) -> tuple[datetime, datetime]:
    """The requested window, clamped into [incident start - 30 min, incident end + 30 min]
    and never past ``now``. Missing bounds default to the incident window. ``not_before``
    raises the lower bound: what came before a testbed reset belongs to another run."""
    low = (
        incident_start - MARGIN if not_before is None else max(incident_start - MARGIN, not_before)
    )
    high = min(now, (incident_end or now) + MARGIN)
    start = max(low, requested_start or incident_start)
    end = min(high, requested_end or incident_end or now)
    if start >= end:
        raise WindowError("the window is empty inside the incident window ± 30 min")
    return start, end
