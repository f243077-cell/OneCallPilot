"""Evidence summaries per kind and the toolbox's refusal paths (architecture §5.3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from oncallpilot.agent import toolbox as toolbox_module
from oncallpilot.agent.providers.embeddings import FakeEmbeddingProvider
from oncallpilot.agent.summaries import summarise
from oncallpilot.agent.toolbox import ToolBox, ToolRefusedError
from oncallpilot.agent.tools.clients import LokiClient, PrometheusClient

T0 = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
LEDGER = next(
    (Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "ledger").glob("*.jsonl")
)


@pytest.mark.parametrize(
    ("kind", "params", "payload", "expected"),
    [
        (
            "log_query",
            {"service": "api", "level": "ERROR", "contains": "Index"},
            {"total_count": 41, "top_exception_types": [{"exc_type": "IndexError", "count": 41}]},
            "41 ERROR log lines on api containing 'Index'; top: IndexError x41",
        ),
        ("log_query", {"service": "lb", "level": "ANY"}, {"total_count": 0}, "0 log lines on lb"),
        (
            "metric_query",
            {},
            {
                "template": "error_rate",
                "service": "api",
                "baseline_mean": 0.004,
                "peak": 0.31,
                "pct_change": 7650.0,
            },
            "error_rate on api: baseline 0.004, peak 0.31 (+7650%)",
        ),
        (
            "metric_query",
            {},
            {
                "template": "memory_rss",
                "service": "worker",
                "note": "no data for this series in the window",
            },
            "memory_rss on worker: no data for this series in the window",
        ),
        (
            "metric_query",
            {},
            {
                "template": "cpu_usage",
                "service": "api",
                "baseline_mean": None,
                "peak": 1.0,
                "pct_change": None,
            },
            "cpu_usage on api: baseline n/a, peak 1",
        ),
        (
            "deploy_list",
            {},
            {
                "records": [
                    {
                        "service": "api",
                        "release": "1.5.0",
                        "kind": "deploy",
                        "deployed_at": "2026-10-21T09:58:00Z",
                    }
                ]
            },
            "1 ledger records; latest: api 1.5.0 (deploy) at 2026-10-21T09:58:00Z",
        ),
        ("deploy_list", {}, {"records": []}, "no deploy-ledger records"),
        (
            "service_health",
            {},
            {
                "service": "worker",
                "containers": [
                    {"state": "running", "restart_count": 3},
                    {"state": "exited", "restart_count": 0},
                ],
                "probe": {"ok": False},
            },
            "worker: 1/2 containers running, 3 restarts; /healthz failing",
        ),
        (
            "service_health",
            {},
            {
                "service": "redis",
                "containers": [{"state": "running", "restart_count": 0}],
                "probe": None,
            },
            "redis: 1/1 containers running, 0 restarts",
        ),
        (
            "runbook_hit",
            {},
            {"chunks": [{"source": "runbooks/rollback.md", "heading": "When"}]},
            "1 runbook passages; top: runbooks/rollback.md (When)",
        ),
        ("runbook_hit", {}, {"chunks": []}, "no matching runbook passages"),
        ("detector_signal", {}, {}, "detector_signal evidence"),
    ],
)
def test_summaries(
    kind: Any, params: dict[str, Any], payload: dict[str, Any], expected: str
) -> None:
    assert summarise(kind, params, payload) == expected


def test_summaries_are_capped() -> None:
    long = summarise("log_query", {"service": "api", "contains": "x" * 400}, {"total_count": 1})
    assert len(long) == 300


def make_box(handler: Any) -> ToolBox:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ToolBox(
        loki=LokiClient(client, "http://loki"),
        prometheus=PrometheusClient(client, "http://prom"),
        docker=client,
        http=client,
        ledger=LEDGER,
        pool=cast(Any, None),
        embedder=FakeEmbeddingProvider(),
    )


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "success", "data": {"result": []}})


async def test_unknown_tool_and_invalid_parameters_are_refused() -> None:
    box = make_box(ok)
    with pytest.raises(ToolRefusedError, match="unknown tool"):
        await box.run("run_shell", {}, T0, None, T0)
    with pytest.raises(ToolRefusedError, match="unknown tool"):
        await box.run("propose_action", {}, T0, None, T0)
    with pytest.raises(ToolRefusedError, match="invalid parameters for query_logs"):
        await box.run("query_logs", {"service": "api", "contains": "x" * 65}, T0, None, T0)


async def test_backend_failure_and_timeout_are_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(ToolRefusedError, match="failed: QueryError"):
        await make_box(broken).run(
            "query_metrics",
            {"template": "error_rate", "service": "api"},
            T0,
            None,
            T0.replace(minute=5),
        )

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(1)

    monkeypatch.setattr(toolbox_module, "TOOL_TIMEOUT_SECONDS", 0.01)
    box = make_box(ok)
    monkeypatch.setattr(box, "_call", slow)
    with pytest.raises(ToolRefusedError, match="timed out"):
        await box.run("get_recent_deploys", {}, T0, None, T0)


async def test_successful_call_returns_kind_params_and_payload() -> None:
    result = await make_box(ok).run(
        "get_recent_deploys", {"service": "api", "limit": 2}, T0, None, T0
    )
    assert result.kind == "deploy_list"
    assert result.params == {"service": "api", "limit": 2}
    assert len(result.payload["records"]) <= 2
