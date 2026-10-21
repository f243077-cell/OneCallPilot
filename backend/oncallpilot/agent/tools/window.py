"""The time window a tool may read: the incident window ± 30 minutes (§6.3, AI-005)."""

from __future__ import annotations

from datetime import datetime, timedelta

MARGIN = timedelta(minutes=30)


class WindowError(ValueError):
    """The requested window is empty after clamping."""


def clamp(
    requested_start: datetime | None,
    requested_end: datetime | None,
    incident_start: datetime,
    incident_end: datetime | None,
    now: datetime,
) -> tuple[datetime, datetime]:
    """The requested window, clamped into [incident start - 30 min, incident end + 30 min]
    and never past ``now``. Missing bounds default to the incident window."""
    low = incident_start - MARGIN
    high = min(now, (incident_end or now) + MARGIN)
    start = max(low, requested_start or incident_start)
    end = min(high, requested_end or incident_end or now)
    if start >= end:
        raise WindowError("the window is empty inside the incident window ± 30 min")
    return start, end
