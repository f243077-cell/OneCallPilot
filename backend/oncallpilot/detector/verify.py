"""Is a triggering signal back to normal? (architecture §5.10 step 7; FR-014, FR-016).

Signal verification after an action must not depend on the detector's
in-memory baselines (a worker restart loses them), so it judges each
triggering signal against fixed bounds instead, more strictly than the
detector itself:

- statistical signals: below the rule's absolute floors (``min_value``,
  ``min_seconds``, ``min_factor`` × the baseline recorded in the alert);
  below a floor a value can never be an anomaly;
- ``restarts``: a container runs and fewer than ``min_restarts`` restarts of
  running containers within the window;
- ``stack_traces``: fewer than ``min_per_minute`` tracebacks per minute.

No data is never normal.
"""

from __future__ import annotations

from oncallpilot.detector.config import DetectorConfig, StatisticalRule
from oncallpilot.detector.engine import Samples

STATISTICAL = ("error_rate", "p95_latency", "job_failure_rate")


def _floor(rule: StatisticalRule, baseline: float | None) -> float:
    floor = max(rule.min_value, rule.min_seconds, rule.min_factor * (baseline or 0.0))
    if floor <= 0 and baseline:
        floor = 2 * baseline  # no absolute floor configured: twice the old normal
    return floor


def signal_normal(
    config: DetectorConfig, name: str, sample: Samples | None, baseline: float | None
) -> bool:
    if sample is None:
        return False
    rules = config.signals
    if name in STATISTICAL:
        value: float | None = getattr(sample, name)
        if value is None:
            return False
        return value < _floor(getattr(rules, name), baseline)
    if name == "restarts":
        r = sample.restarts
        return (
            r is not None and r.running > 0 and r.restarts_in_window < rules.restarts.min_restarts
        )
    if name == "stack_traces":
        t = sample.stack_traces
        return t is not None and t.per_minute < rules.stack_traces.min_per_minute
    return False
