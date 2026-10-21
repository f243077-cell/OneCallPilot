"""The 12 fixed PromQL templates of ``query_metrics`` (architecture §6.3; C7 §2.6; AI-006).

Only the template name and the service come from the model, and both are
enums: the PromQL text is fixed here and never built from free text.
"""

from __future__ import annotations

from typing import Literal

from oncallpilot_contracts.enums import MetricTemplate

MetricService = Literal["api", "worker"]

_HTTP_5XX_SHARE = (
    'sum(rate(http_requests_total{{service="{s}",status=~"5.."}}[1m]))'
    ' / sum(rate(http_requests_total{{service="{s}"}}[1m]))'
)

# template -> (PromQL with {s} for the service, services it applies to)
TEMPLATES: dict[MetricTemplate, tuple[str, tuple[MetricService, ...]]] = {
    "error_rate": (_HTTP_5XX_SHARE, ("api",)),
    "p95_latency": (
        "histogram_quantile(0.95, sum by (le) "
        '(rate(http_request_duration_seconds_bucket{{service="{s}"}}[1m])))',
        ("api",),
    ),
    "request_rate": ('sum(rate(http_requests_total{{service="{s}"}}[1m]))', ("api",)),
    "memory_rss": ('max(process_resident_memory_bytes{{service="{s}"}})', ("api", "worker")),
    "cpu_usage": ('sum(rate(process_cpu_seconds_total{{service="{s}"}}[1m]))', ("api", "worker")),
    "process_restarts": (
        'sum(changes(process_start_time_seconds{{service="{s}"}}[5m]))',
        ("api", "worker"),
    ),
    "db_pool_in_use": ('sum(db_pool_connections{{service="{s}",state="in_use"}})', ("api",)),
    "db_pool_wait_p95": (
        "histogram_quantile(0.95, sum by (le) "
        '(rate(db_pool_wait_seconds_bucket{{service="{s}"}}[1m])))',
        ("api",),
    ),
    "cache_errors": (
        'sum(rate(cache_operations_total{{service="{s}",result="error"}}[1m]))',
        ("api",),
    ),
    "upstream_latency_p95": (
        "histogram_quantile(0.95, sum by (le) "
        '(rate(upstream_request_duration_seconds_bucket{{service="{s}",upstream="payments"}}[1m])))',
        ("api",),
    ),
    "job_failure_rate": (
        'sum(rate(worker_jobs_total{{service="{s}",result="error"}}[1m]))'
        ' / sum(rate(worker_jobs_total{{service="{s}"}}[1m]))',
        ("worker",),
    ),
    "job_latency_p95": (
        "histogram_quantile(0.95, sum by (le) "
        '(rate(worker_job_duration_seconds_bucket{{service="{s}"}}[1m])))',
        ("worker",),
    ),
}


class UnsupportedMetricError(ValueError):
    """The template does not exist, or does not apply to that service."""


_BY_NAME: dict[str, tuple[str, tuple[MetricService, ...]]] = {k: v for k, v in TEMPLATES.items()}


def render(template: str, service: str) -> str:
    entry = _BY_NAME.get(template)
    if entry is None:
        raise UnsupportedMetricError(f"unknown metric template {template!r}")
    promql, services = entry
    if service not in services:
        raise UnsupportedMetricError(f"{template} is measured on {', '.join(services)} only")
    return promql.format(s=service)
