"""``query_metrics`` (architecture §6.3; AI-006): Prometheus through 12 fixed PromQL templates.

Returns a ``MetricQueryPayload`` (C1): at most 120 points, plus the baseline
mean (points before the first anomaly, else the first quarter of the window),
the peak, the % change from baseline to peak, and the change point (the split
that best separates two levels).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from oncallpilot_contracts.common import UtcDatetime
from oncallpilot_contracts.domain import METRIC_UNITS, MetricPoint, MetricQueryPayload
from oncallpilot_contracts.enums import MetricTemplate
from pydantic import BaseModel, ConfigDict, Field

from oncallpilot.agent.tools.clients import PrometheusClient
from oncallpilot.agent.tools.promql import MetricService, render
from oncallpilot.agent.tools.window import clamp

MAX_POINTS = 120
MIN_SEGMENT = 3
# The incident window opens 10 minutes before the first anomaly (FR-007).
ANOMALY_LEAD = timedelta(minutes=10)


class QueryMetricsParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    template: MetricTemplate
    service: MetricService
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    step_seconds: int = Field(default=15, ge=5, le=3600)


def step_for(start: datetime, end: datetime, wanted: int) -> int:
    """The smallest step ≥ ``wanted`` that gives at most 120 points."""
    seconds = (end - start).total_seconds()
    return max(wanted, math.ceil(seconds / (MAX_POINTS - 1)))


def change_point(values: list[float]) -> int | None:
    """Index where the series shifts level most (largest difference of segment means)."""
    if len(values) < 2 * MIN_SEGMENT:
        return None
    best, best_gap = None, 0.0
    for i in range(MIN_SEGMENT, len(values) - MIN_SEGMENT + 1):
        left = sum(values[:i]) / i
        right = sum(values[i:]) / (len(values) - i)
        gap = abs(right - left)
        if gap > best_gap:
            best, best_gap = i, gap
    return best


async def query_metrics(
    prometheus: PrometheusClient,
    params: QueryMetricsParams,
    incident_start: datetime,
    incident_end: datetime | None,
    now: datetime,
) -> MetricQueryPayload:
    promql = render(params.template, params.service)
    start, end = clamp(params.start, params.end, incident_start, incident_end, now)
    step = step_for(start, end, params.step_seconds)
    raw = await prometheus.query_range(promql, start, end, step)
    points = [MetricPoint(ts=datetime.fromtimestamp(ts, UTC), value=v) for ts, v in raw]
    points = points[-MAX_POINTS:]
    unit = METRIC_UNITS[params.template]
    if not points:
        return MetricQueryPayload(
            template=params.template,
            service=params.service,
            unit=unit,
            step_seconds=step,
            points=[],
            baseline_mean=None,
            peak=None,
            pct_change=None,
            change_point_at=None,
            note="no data for this series in the window",
        )
    values = [p.value for p in points]
    anomaly = incident_start + ANOMALY_LEAD
    before = [p.value for p in points if p.ts < anomaly]
    if len(before) < 2:
        before = values[: max(1, len(values) // 4)]
    baseline = sum(before) / len(before)
    peak = max(values)
    pct = round((peak - baseline) / baseline * 100, 1) if baseline > 0 else None
    split = change_point(values)
    return MetricQueryPayload(
        template=params.template,
        service=params.service,
        unit=unit,
        step_seconds=step,
        points=points,
        baseline_mean=baseline,
        peak=peak,
        pct_change=pct,
        change_point_at=points[split].ts if split is not None else None,
        note=None,
    )
