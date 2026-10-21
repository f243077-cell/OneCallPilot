"""LiveSampleSource: Prometheus, Loki and socket-proxy-ro into detector Samples (FR-001,
FR-002; architecture §6.8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from oncallpilot.agent.tools.clients import LokiClient, PrometheusClient
from oncallpilot.detector.config import load_config, read_config
from oncallpilot.detector.sources import LiveSampleSource, RestartHistory
from tests.test_detector_config import CONFIG

AT = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def prom_value(value: str | None) -> httpx.Response:
    result = [] if value is None else [{"metric": {}, "value": [0, value]}]
    return httpx.Response(200, json={"status": "success", "data": {"result": result}})


def prometheus(values: dict[str, str | None], down: bool = False) -> PrometheusClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if down:
            return httpx.Response(503)
        query = parse_qs(urlparse(str(request.url)).query)["query"][0]
        for marker, value in values.items():
            if marker in query:
                return prom_value(value)
        return prom_value(None)

    return PrometheusClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "http://p")


def loki(series: list[tuple[str, float]]) -> LokiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        result = [{"metric": {"exc_type": t}, "value": [0, str(v)]} for t, v in series]
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    return LokiClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), "http://l")


class Docker:
    def __init__(self, containers: dict[str, dict[str, Any]]) -> None:
        self.containers = containers
        self.methods: set[str] = set()

    def client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.methods.add(request.method)
            if request.url.path == "/containers/json":
                return httpx.Response(200, json=[{"Id": cid} for cid in self.containers])
            cid = request.url.path.split("/")[2]
            return httpx.Response(200, json=self.containers[cid])

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://proxy")


def container(
    name: str,
    service: str,
    status: str = "running",
    restarts: int = 0,
    exit_code: int = 0,
    finished: datetime | None = None,
) -> dict[str, Any]:
    return {
        "Name": f"/{name}",
        "RestartCount": restarts,
        "State": {
            "Status": status,
            "ExitCode": exit_code,
            "FinishedAt": (finished or datetime(1, 1, 1, tzinfo=UTC))
            .isoformat()
            .replace("+00:00", "Z")
            if finished
            else "0001-01-01T00:00:00Z",
            "Env": ["SECRET=never-read"],
        },
        "Config": {"Labels": {"oncallpilot.service": service}, "Env": ["X=1"]},
    }


def source(docker: Docker, prom: PrometheusClient, logs: LokiClient) -> LiveSampleSource:
    return LiveSampleSource(load_config(read_config(CONFIG)), prom, logs, docker.client())


async def test_collects_every_signal_per_service() -> None:
    docker = Docker(
        {
            "a": container("cs-api-140-1", "api"),
            "w": container("cs-worker-210", "worker"),
            "r": container("cs-redis", "redis", status="exited", exit_code=0),
            "s": container(
                "cs-api-150-1",
                "api",
                status="exited",
                exit_code=137,
                finished=AT - timedelta(hours=2),
            ),  # fmt: skip
        }
    )
    prom = prometheus(
        {'status=~"5.."': "0.31", "http_request_duration": "0.42", "worker_jobs": "0"}
    )
    out = await source(docker, prom, loki([("KeyError", 3), ("ValueError", 4)])).collect(AT)

    assert out["api"].error_rate == 0.31 and out["api"].p95_latency == 0.42
    assert out["worker"].job_failure_rate == 0.0 and out["worker"].error_rate is None
    api_traces = out["api"].stack_traces
    assert api_traces is not None and api_traces.per_minute == 7
    assert api_traces.exception_types == {"KeyError", "ValueError"}
    api_restarts = out["api"].restarts
    # An old SIGKILLed slot is not a crash now; one api container runs.
    assert api_restarts is not None and api_restarts.running == 1
    assert not api_restarts.exited_nonzero
    redis = out["redis"].restarts
    assert redis is not None and redis.running == 0  # scenario 4: stopped cache
    assert out["redis"].error_rate is None and out["redis"].stack_traces is None
    assert docker.methods == {"GET"}


async def test_restarts_counted_within_the_window_and_recent_crash_flagged() -> None:
    worker = container("cs-worker-220", "worker", restarts=1)
    docker = Docker({"w": worker})
    src = source(docker, prometheus({}), loki([]))
    first = (await src.collect(AT))["worker"].restarts
    assert first is not None and first.restarts_in_window == 0
    worker["RestartCount"] = 3
    worker["State"].update(
        Status="exited",
        ExitCode=1,
        FinishedAt=(AT + timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
    )
    later = (await src.collect(AT + timedelta(seconds=30)))["worker"].restarts
    assert later is not None and later.restarts_in_window == 2
    assert later.exited_nonzero and later.running == 0


async def test_unavailable_sources_leave_values_out() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    src = LiveSampleSource(
        load_config(read_config(CONFIG)),
        prometheus({}, down=True),
        LokiClient(httpx.AsyncClient(transport=httpx.MockTransport(broken)), "http://l"),
        httpx.AsyncClient(transport=httpx.MockTransport(broken), base_url="http://proxy"),
    )
    out = await src.collect(AT)
    assert all(
        s.error_rate is None and s.restarts is None and s.stack_traces is None for s in out.values()
    )


async def test_no_traffic_reads_as_missing() -> None:
    prom = prometheus({'status=~"5.."': "NaN"})
    out = await source(Docker({}), prom, loki([])).collect(AT)
    assert out["api"].error_rate is None


def test_restart_history_handles_a_recreated_container() -> None:
    history = RestartHistory(timedelta(seconds=300))
    assert history.restarts("c", 5, AT) == 0
    assert history.restarts("c", 7, AT + timedelta(seconds=60)) == 2
    assert history.restarts("c", 0, AT + timedelta(seconds=120)) == 0
    assert history.restarts("c", 1, AT + timedelta(seconds=180)) == 1
    # Older than the window: no longer counted.
    assert history.restarts("c", 1, AT + timedelta(seconds=600)) == 0
