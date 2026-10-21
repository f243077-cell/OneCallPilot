"""get_recent_deploys, get_service_health, embeddings and chunking (AI-007, AI-008, AI-026)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import ValidationError

from oncallpilot.agent.providers.embeddings import (
    DIMENSIONS,
    FakeEmbeddingProvider,
    normalise,
)
from oncallpilot.agent.tools.get_recent_deploys import get_recent_deploys
from oncallpilot.agent.tools.get_service_health import (
    docker_base_url,
    get_service_health,
    project,
)
from oncallpilot.agent.tools.params import (
    GetRecentDeploysParams,
    GetServiceHealthParams,
    SearchRunbooksParams,
)
from oncallpilot.runbooks.ingest import MAX_CHUNK_CHARS, split_markdown

LEDGER = next(
    (Path(__file__).resolve().parents[3] / "contracts" / "fixtures" / "ledger").glob("*.jsonl")
)

# --- get_recent_deploys ---------------------------------------------------------


def test_recent_deploys_newest_first_without_hidden_fields() -> None:
    payload = get_recent_deploys(LEDGER, GetRecentDeploysParams())
    times = [r.deployed_at for r in payload.records]
    assert times == sorted(times, reverse=True)
    assert 1 <= len(payload.records) <= 10
    dumped = json.dumps(payload.model_dump(mode="json"))
    assert "deploy_id" not in dumped and "image_tag" not in dumped


def test_recent_deploys_filter_and_limit() -> None:
    worker = get_recent_deploys(LEDGER, GetRecentDeploysParams(service="worker", limit=2))
    assert len(worker.records) <= 2
    assert {r.service for r in worker.records} <= {"worker"}
    assert get_recent_deploys(LEDGER.parent / "none.jsonl", GetRecentDeploysParams()).records == []
    with pytest.raises(ValidationError):
        GetRecentDeploysParams(limit=11)
    with pytest.raises(ValidationError):
        GetRecentDeploysParams.model_validate({"service": "payments"})


# --- get_service_health ---------------------------------------------------------

INSPECT: dict[str, Any] = {
    "Id": "abc",
    "Name": "/cs-api-150-1",
    "RestartCount": 3,
    "State": {
        "Status": "running",
        "OOMKilled": False,
        "ExitCode": 0,
        "StartedAt": "2026-10-21T09:58:01.123456789Z",
        "Health": {"Status": "unhealthy", "Log": [{"Output": "SECRET_TOKEN=abc"}]},
    },
    "Config": {
        "Env": ["CS_DATABASE_URL=postgresql://shop:hunter2@cs-postgres/shop"],
        "Labels": {
            "oncallpilot.release": "1.5.0",
            "oncallpilot.service": "api",
            "com.docker.compose.project.config_files": "/home/me/secret/compose.yaml",
        },
        "Cmd": ["python", "-m", "shop"],
    },
    "Mounts": [{"Source": "/var/run/docker.sock"}],
    "HostConfig": {"Binds": ["/etc:/host-etc"]},
}


def test_projection_keeps_only_the_allowed_fields() -> None:
    health = project(INSPECT)
    assert health.model_dump(mode="json") == {
        "name": "cs-api-150-1",
        "release": "1.5.0",
        "state": "running",
        "health": "unhealthy",
        "restart_count": 3,
        "oom_killed": False,
        "exit_code": None,
        "started_at": "2026-10-21T09:58:01.123456Z",
    }
    text = json.dumps(health.model_dump(mode="json"))
    for leaked in ("hunter2", "SECRET_TOKEN", "docker.sock", "compose.yaml", "host-etc", "shop"):
        assert leaked not in text


def test_projection_of_an_exited_container() -> None:
    exited = {
        **INSPECT,
        "State": {
            "Status": "exited",
            "ExitCode": 137,
            "OOMKilled": True,
            "StartedAt": "0001-01-01T00:00:00Z",
        },
    }
    health = project(exited)
    assert (health.state, health.exit_code, health.oom_killed, health.started_at) == (
        "exited",
        137,
        True,
        None,
    )
    assert health.health == "none"


async def test_service_health_reads_only_through_get() -> None:
    methods: list[tuple[str, str]] = []

    def docker(request: httpx.Request) -> httpx.Response:
        methods.append((request.method, request.url.path))
        if request.url.path == "/containers/json":
            filters = json.loads(parse_qs(urlparse(str(request.url)).query)["filters"][0])
            assert filters == {"label": ["oncallpilot.managed=true", "oncallpilot.service=api"]}
            return httpx.Response(200, json=[{"Id": "abc", "Names": ["/cs-api-150-1"]}])
        return httpx.Response(200, json=INSPECT)

    def chaos(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://api-upstream:8000/healthz"
        return httpx.Response(500)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(docker), base_url="http://proxy") as d,
        httpx.AsyncClient(transport=httpx.MockTransport(chaos)) as h,
    ):
        payload = await get_service_health(d, h, GetServiceHealthParams(service="api"))
    assert {m for m, _ in methods} == {"GET"}
    assert [c.name for c in payload.containers] == ["cs-api-150-1"]
    assert (
        payload.probe is not None and payload.probe.ok is False and payload.probe.status_code == 500
    )


async def test_services_without_a_probe() -> None:
    def docker(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(docker), base_url="http://proxy") as d,
        httpx.AsyncClient(transport=httpx.MockTransport(boom)) as h,
    ):
        redis = await get_service_health(d, h, GetServiceHealthParams(service="redis"))
        payments = await get_service_health(d, h, GetServiceHealthParams(service="payments"))
    assert redis.probe is None and redis.containers == []
    assert payments.probe is not None and payments.probe.error == "ConnectError"


def test_docker_host_must_be_the_tcp_proxy() -> None:
    assert docker_base_url("tcp://socket-proxy-ro:2375") == "http://socket-proxy-ro:2375"
    for bad in ("unix:///var/run/docker.sock", "npipe:////./pipe/docker_engine", "http://x"):
        with pytest.raises(ValueError):
            docker_base_url(bad)


# --- embeddings and chunking -----------------------------------------------------


async def test_fake_embeddings_are_normalised_and_similar_for_shared_words() -> None:
    embedder = FakeEmbeddingProvider()
    a, b, c = await embedder.embed(
        ["rollback a bad deploy", "how to rollback a deploy", "redis cache errors"],
        "RETRIEVAL_DOCUMENT",
    )
    assert len(a) == DIMENSIONS
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0, rel_tol=1e-9)

    def cos(x: list[float], y: list[float]) -> float:
        return sum(p * q for p, q in zip(x, y, strict=True))

    assert cos(a, b) > cos(a, c)
    with pytest.raises(ValueError):
        normalise([0.0, 0.0])


def test_markdown_is_split_at_headings_and_long_sections() -> None:
    long = "\n\n".join(f"Paragraph {i}. " + "word " * 80 for i in range(12))
    text = f"Intro line.\n\n# Rollback\n\nSteps here.\n\n## Checks\n\n{long}\n"
    chunks = split_markdown("runbooks/rollback.md", text)
    assert chunks[0].heading is None and chunks[0].content == "Intro line."
    assert chunks[1].heading == "Rollback"
    assert all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert sum(c.heading == "Checks" for c in chunks) >= 2


def test_search_params_are_strict() -> None:
    with pytest.raises(ValidationError):
        SearchRunbooksParams(query="x", k=4)
    with pytest.raises(ValidationError):
        SearchRunbooksParams(query="x" * 201)
