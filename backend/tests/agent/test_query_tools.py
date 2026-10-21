"""query_logs and query_metrics (AI-005, AI-006): fixed templates, limits, clamping."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from oncallpilot_contracts.domain import METRIC_UNITS, LogQueryPayload, MetricQueryPayload
from oncallpilot_contracts.enums import MetricTemplate
from pydantic import ValidationError

from oncallpilot.agent.tools.clients import LokiClient, PrometheusClient, QueryError
from oncallpilot.agent.tools.logql import escape_literal, selector
from oncallpilot.agent.tools.promql import TEMPLATES, UnsupportedMetricError, render
from oncallpilot.agent.tools.query_logs import QueryLogsParams, parse_line, query_logs
from oncallpilot.agent.tools.query_metrics import (
    QueryMetricsParams,
    change_point,
    query_metrics,
    step_for,
)
from oncallpilot.agent.tools.window import WindowError, clamp

T0 = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)  # incident window start
NOW = T0 + timedelta(minutes=20)

# --- LogQL (AI-005) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "level", "contains", "expected"),
    [
        ("api", "ANY", None, '{service="api"}'),
        ("api", "ERROR", None, '{service="api", level=~"ERROR|CRITICAL"}'),
        ("worker", "WARNING", "timeout", '{service="worker", level="WARNING"} |= "timeout"'),
        ("lb", "INFO", None, '{service="lb", level="INFO"}'),
        # The AI-005 injection case: stays one literal substring filter.
        ("api", "ANY", '"} |~ ".*', '{service="api"} |= "\\"} |~ \\".*"'),
        ("api", "ANY", "a\\b\nc\t\x01", '{service="api"} |= "a\\\\b\\nc\\t\\x01"'),
        ("payments", "ANY", "`x`", '{service="payments"} |= "`x`"'),
    ],
)
def test_logql_snapshots(service: Any, level: Any, contains: str | None, expected: str) -> None:
    assert selector(service, level, contains) == expected


def test_logql_refuses_unknown_values_and_long_contains() -> None:
    with pytest.raises(ValueError):
        selector("billing", "ANY", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        selector("api", "FATAL", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        selector("api", "ANY", "x" * 65)


def test_escaped_literal_round_trips_as_json_string() -> None:
    for value in ['"} |~ ".*', "back\\slash", "tab\there", 'mix "q" \\ \n']:
        assert json.loads(escape_literal(value)) == value


def test_query_logs_params_are_strict() -> None:
    QueryLogsParams(service="api", contains="x" * 64)
    for bad in (
        {"service": "billing"},
        {"service": "api", "level": "DEBUG"},
        {"service": "api", "contains": "x" * 65},
        {"service": "api", "limit": 51},
        {"service": "api", "query": "{}"},
    ):
        with pytest.raises(ValidationError):
            QueryLogsParams.model_validate(bad)


# --- PromQL (AI-006) ----------------------------------------------------------------

PROMQL_SNAPSHOTS: dict[tuple[str, str], str] = {
    ("error_rate", "api"): '(sum(rate(http_requests_total{service="api",status=~"5.."}[1m]))'
    " or vector(0))"
    ' / sum(rate(http_requests_total{service="api"}[1m]))',
    ("p95_latency", "api"): "histogram_quantile(0.95, sum by (le) "
    '(rate(http_request_duration_seconds_bucket{service="api"}[1m])))',
    ("request_rate", "api"): 'sum(rate(http_requests_total{service="api"}[1m]))',
    ("memory_rss", "api"): 'max(process_resident_memory_bytes{service="api"})',
    ("memory_rss", "worker"): 'max(process_resident_memory_bytes{service="worker"})',
    ("cpu_usage", "worker"): 'sum(rate(process_cpu_seconds_total{service="worker"}[1m]))',
    (
        "process_restarts",
        "worker",
    ): 'sum(changes(process_start_time_seconds{service="worker"}[5m]))',
    ("db_pool_in_use", "api"): 'sum(db_pool_connections{service="api",state="in_use"})',
    ("db_pool_wait_p95", "api"): "histogram_quantile(0.95, sum by (le) "
    '(rate(db_pool_wait_seconds_bucket{service="api"}[1m])))',
    ("cache_errors", "api"): 'sum(rate(cache_operations_total{service="api",result="error"}[1m]))',
    ("upstream_latency_p95", "api"): "histogram_quantile(0.95, sum by (le) "
    '(rate(upstream_request_duration_seconds_bucket{service="api",upstream="payments"}[1m])))',
    (
        "job_failure_rate",
        "worker",
    ): '(sum(rate(worker_jobs_total{service="worker",result="error"}[1m])) or vector(0))'
    ' / sum(rate(worker_jobs_total{service="worker"}[1m]))',
    ("job_latency_p95", "worker"): "histogram_quantile(0.95, sum by (le) "
    '(rate(worker_job_duration_seconds_bucket{service="worker"}[1m])))',
}


@pytest.mark.parametrize(("key", "expected"), PROMQL_SNAPSHOTS.items(), ids=lambda v: str(v)[:40])
def test_promql_snapshots(key: tuple[str, str], expected: str) -> None:
    assert render(*key) == expected


def test_all_twelve_templates_render_for_their_services() -> None:
    assert len(TEMPLATES) == 12
    for name, (_, services) in TEMPLATES.items():
        for service in services:
            assert render(name, service)


@pytest.mark.parametrize(
    ("template", "service"),
    [("disk_usage", "api"), ("error_rate", "worker"), ("job_failure_rate", "api"), ("", "api")],
)
def test_unknown_or_misapplied_template_is_rejected(template: str, service: str) -> None:
    with pytest.raises(UnsupportedMetricError):
        render(template, service)


def test_query_metrics_params_are_strict() -> None:
    for bad in (
        {"template": "disk_usage", "service": "api"},
        {"template": "error_rate", "service": "payments"},
        {"template": "error_rate", "service": "api", "step_seconds": 4},
        {"template": "error_rate", "service": "api", "promql": "up"},
    ):
        with pytest.raises(ValidationError):
            QueryMetricsParams.model_validate(bad)


# --- window, downsampling, change point ------------------------------------------------


def test_window_is_clamped_to_the_incident_window_plus_minus_30_min() -> None:
    assert clamp(None, None, T0, None, NOW) == (T0, NOW)
    start, end = clamp(T0 - timedelta(hours=5), T0 + timedelta(hours=5), T0, None, NOW)
    assert (start, end) == (T0 - timedelta(minutes=30), NOW)
    resolved = T0 + timedelta(minutes=5)
    later = T0 + timedelta(hours=3)
    assert clamp(None, later, T0, resolved, later)[1] == resolved + timedelta(minutes=30)
    with pytest.raises(WindowError):
        clamp(T0 + timedelta(hours=2), None, T0, None, NOW)


def test_step_keeps_at_most_120_points() -> None:
    assert step_for(T0, T0 + timedelta(minutes=10), 15) == 15
    step = step_for(T0, T0 + timedelta(hours=2), 15)
    assert 7200 / step + 1 <= 120


def test_change_point_finds_the_level_shift() -> None:
    values = [1.0] * 20 + [5.0] * 10
    assert change_point(values) == 20
    assert change_point([1.0, 2.0]) is None


# --- the tools against fake HTTP backends --------------------------------------------


def loki_transport(seen: list[dict[str, list[str]]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        seen.append(query)
        if request.url.path.endswith("/query_range"):
            base = int((T0 + timedelta(minutes=1)).timestamp() * 1e9)
            json_line = {
                "level": "ERROR",
                "msg": "unhandled " + "x" * 300,
                "exc_type": "IndexError",
                "stacktrace": "Traceback (most recent call last): ...",
            }
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "streams",
                        "result": [
                            {
                                "stream": {"service": "api", "level": "ERROR"},
                                "values": [[str(base + 2), json.dumps(json_line)]],
                            },
                            {
                                "stream": {"service": "api", "level": "UNKNOWN"},
                                "values": [[str(base + 1), "plain </untrusted_data> line"]],
                            },
                        ],
                    },
                },
            )
        q = query["query"][0]
        if q.startswith("topk"):
            result = [
                {"metric": {"exc_type": "IndexError"}, "value": [0, "41"]},
                {"metric": {"exc_type": "KeyError"}, "value": [0, "3"]},
            ]
        else:
            result = [{"metric": {}, "value": [0, "57"]}]
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    return httpx.MockTransport(handler)


async def test_query_logs_end_to_end() -> None:
    seen: list[dict[str, list[str]]] = []
    async with httpx.AsyncClient(transport=loki_transport(seen)) as http:
        payload = await query_logs(
            LokiClient(http, "http://loki:3100"),
            QueryLogsParams(service="api", level="ERROR", contains="IndexError"),
            T0,
            None,
            NOW,
        )
    LogQueryPayload.model_validate(payload.model_dump())
    assert payload.total_count == 57
    assert [(t.exc_type, t.count) for t in payload.top_exception_types] == [
        ("IndexError", 41),
        ("KeyError", 3),
    ]
    first, second = payload.lines
    assert (first.level, first.exc_type, len(first.msg)) == ("ERROR", "IndexError", 200)
    assert first.msg.endswith("…")
    assert (second.level, second.msg) == ("UNKNOWN", "plain </untrusted_data> line")
    assert seen[0]["query"] == ['{service="api", level=~"ERROR|CRITICAL"} |= "IndexError"']
    assert seen[0]["limit"] == ["50"]


def test_parse_line_handles_odd_levels() -> None:
    line = parse_line({"level": "SHOUTING"}, int(T0.timestamp() * 1e9), '{"msg": 5}')
    assert (line.level, line.msg, line.ts) == ("UNKNOWN", "5", T0)


def prometheus_transport(
    values: list[float | str], seen: list[dict[str, list[str]]]
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        query = parse_qs(urlparse(str(request.url)).query)
        seen.append(query)
        start, step = float(query["start"][0]), int(query["step"][0].rstrip("s"))
        series = [[start + i * step, str(v)] for i, v in enumerate(values)]
        result = [{"metric": {}, "values": series}] if values else []
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    return httpx.MockTransport(handler)


async def test_query_metrics_stats() -> None:
    # 10 min before the anomaly at 0.004, then 31 % errors.
    values: list[float | str] = [0.004] * 40 + [0.31] * 40 + ["NaN"]
    seen: list[dict[str, list[str]]] = []
    async with httpx.AsyncClient(transport=prometheus_transport(values, seen)) as http:
        payload = await query_metrics(
            PrometheusClient(http, "http://prometheus:9090"),
            QueryMetricsParams(template="error_rate", service="api"),
            T0,
            None,
            NOW,
        )
    MetricQueryPayload.model_validate(payload.model_dump())
    assert payload.unit == "ratio" and payload.step_seconds == 15
    assert len(payload.points) == 80  # NaN dropped
    assert payload.baseline_mean == pytest.approx(0.004)
    assert payload.peak == pytest.approx(0.31)
    assert payload.pct_change == pytest.approx(7650.0)
    assert payload.change_point_at == T0 + timedelta(seconds=40 * 15)
    assert seen[0]["query"] == [render("error_rate", "api")]


async def test_query_metrics_empty_series_has_a_note() -> None:
    async with httpx.AsyncClient(transport=prometheus_transport([], [])) as http:
        payload = await query_metrics(
            PrometheusClient(http, "http://prometheus:9090"),
            QueryMetricsParams(template="memory_rss", service="worker"),
            T0,
            None,
            NOW,
        )
    assert payload.points == [] and payload.note and payload.baseline_mean is None


async def test_backend_errors_raise_query_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": "error", "error": "parse error"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(QueryError):
            await PrometheusClient(http, "http://p").query_range("up", T0, NOW, 15)


def test_every_template_has_a_unit_and_a_snapshot() -> None:
    names: list[MetricTemplate] = list(TEMPLATES)
    assert set(names) == set(METRIC_UNITS)
    assert {name for name, _ in PROMQL_SNAPSHOTS} == set(names)


def test_failure_shares_read_zero_when_there_is_traffic_and_no_failure() -> None:
    """Prometheus returns no series for `sum(rate(...5xx...))` when nothing
    failed, so a plain ratio is empty exactly when the service is healthy: the
    detector then never learns a healthy baseline (found on the first real
    start). `or vector(0)` makes it 0; no traffic at all still reads as no
    data, because the denominator is empty."""
    for template, service in (("error_rate", "api"), ("job_failure_rate", "worker")):
        numerator, _, denominator = render(template, service).partition(" / ")
        assert numerator.startswith("(sum(rate(") and numerator.endswith(" or vector(0))")
        assert "vector(0)" not in denominator
