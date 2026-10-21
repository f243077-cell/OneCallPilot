"""POST /ingest/alert against Postgres and Redis (API-003; FR-004, FR-005, FR-007)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import INGEST_TOKEN, LiveApi, build_services, serve
from oncallpilot_contracts.ws import WsServerMessage
from pydantic import SecretStr

from oncallpilot.api.app import IngestConfig
from oncallpilot.events import EventStream
from oncallpilot.queues import AGENT_JOBS_STREAM, BENCH_RUN_KEY
from tests.keys import SigningKey

T = datetime(2026, 10, 21, 10, 0, 10, tzinfo=UTC)
HEADERS = {"X-Ingest-Token": INGEST_TOKEN}


def alert(service: str = "api", **signal: Any) -> dict[str, Any]:
    sig = {
        "name": "error_rate",
        "value": 0.31,
        "baseline": 0.004,
        "zscore": 9.2,
        "first_anomalous_at": T.isoformat().replace("+00:00", "Z"),
    }
    sig.update(signal)
    return {
        "source": "detector",
        "service": service,
        "signals": [sig],
        "observed_at": (T + timedelta(seconds=15)).isoformat().replace("+00:00", "Z"),
    }


async def counts(api: LiveApi) -> dict[str, int]:
    return {
        "incidents": await api.pool.fetchval("select count(*) from ocp.incidents"),
        "evidence": await api.pool.fetchval("select count(*) from ocp.evidence"),
        "audit": await api.pool.fetchval("select count(*) from ocp.audit_log"),
        "jobs": await api.redis.xlen(AGENT_JOBS_STREAM),
        "events": await api.redis.xlen("ocp:events"),
    }


@pytest.mark.parametrize(
    "headers", [{}, {"X-Ingest-Token": "wrong"}, {"X-Ingest-Token": ""}, {"Authorization": "x"}]
)
async def test_missing_or_wrong_token_is_401(live_api: LiveApi, headers: dict[str, str]) -> None:
    response = await live_api.client.post("/ingest/alert", json=alert(), headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    # Checked before the body: a bad body without a token is still 401.
    response = await live_api.client.post("/ingest/alert", content=b"{", headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"{}",
        b'{"source": "detector", "service": "api", "signals": [],'
        b' "observed_at": "2026-10-21T10:00:00Z"}',
    ],
)
async def test_invalid_body_is_422(live_api: LiveApi, body: bytes) -> None:
    response = await live_api.client.post("/ingest/alert", content=body, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert (await counts(live_api))["incidents"] == 0


async def test_unknown_service_is_422(live_api: LiveApi) -> None:
    response = await live_api.client.post("/ingest/alert", json=alert("billing"), headers=HEADERS)
    assert response.status_code == 422


async def test_alert_opens_one_incident_with_e1_audit_job_and_event(live_api: LiveApi) -> None:
    response = await live_api.client.post("/ingest/alert", json=alert(), headers=HEADERS)
    assert response.status_code == 202
    body = response.json()
    assert body["deduplicated"] is False
    incident_id = uuid.UUID(body["incident_id"])
    assert await counts(live_api) == {
        "incidents": 1,
        "evidence": 1,
        "audit": 1,
        "jobs": 1,
        "events": 1,
    }
    row = await live_api.pool.fetchrow("select * from ocp.incidents where id = $1", incident_id)
    assert row is not None
    assert row["status"] == "investigating"
    assert row["severity"] == "sev1"
    assert row["title"] == "api error rate 31%"
    assert row["window_start"] == T - timedelta(minutes=10)
    assert row["window_end"] is None
    assert len(row["detector_config_version"]) == 64
    evidence = await live_api.pool.fetchrow("select * from ocp.evidence")
    assert evidence is not None
    assert (evidence["ref"], evidence["kind"], evidence["purpose"]) == (
        "E1",
        "detector_signal",
        "seed",
    )
    audit = await live_api.pool.fetchrow("select actor, event, incident_id from ocp.audit_log")
    assert audit is not None
    assert (audit["actor"], audit["event"], audit["incident_id"]) == (
        "detector",
        "incident.opened",
        incident_id,
    )
    jobs: Any = await live_api.redis.xrange(AGENT_JOBS_STREAM)
    [(_, job)] = jobs
    assert job == {"incident_id": str(incident_id)}
    [event] = await EventStream(live_api.redis).read_after("0-0", block_ms=None)
    WsServerMessage.model_validate(event)
    assert (event["event"], event["state_version"], event["data"]["id"]) == (
        "incident.opened",
        1,
        str(incident_id),
    )
    # The REST view agrees.
    detail = await live_api.pool.fetchval(
        "select trigger->'signals'->0->>'name' from ocp.incidents where id = $1", incident_id
    )
    assert detail == "error_rate"


async def test_twenty_identical_alerts_make_one_incident(live_api: LiveApi) -> None:
    responses = await asyncio.gather(
        *(live_api.client.post("/ingest/alert", json=alert(), headers=HEADERS) for _ in range(20))
    )
    assert {r.status_code for r in responses} == {202}
    ids = {r.json()["incident_id"] for r in responses}
    assert len(ids) == 1
    assert sum(not r.json()["deduplicated"] for r in responses) == 1
    found = await counts(live_api)
    assert found["incidents"] == 1
    assert found["evidence"] == 1  # same signal type: no new evidence
    assert found["jobs"] == 1
    assert found["events"] == 1
    updates = await live_api.pool.fetchval(
        "select jsonb_array_length(trigger->'updates') from ocp.incidents"
    )
    assert updates == 19


async def test_new_signal_type_on_an_open_incident_adds_evidence(live_api: LiveApi) -> None:
    first = await live_api.client.post("/ingest/alert", json=alert(), headers=HEADERS)
    second = await live_api.client.post(
        "/ingest/alert", json=alert(name="stack_traces", value=12, baseline=None), headers=HEADERS
    )
    assert second.json() == {"incident_id": first.json()["incident_id"], "deduplicated": True}
    refs = [
        r["ref"] for r in await live_api.pool.fetch("select ref from ocp.evidence order by ref")
    ]
    assert refs == ["E1", "E2"]
    events = await EventStream(live_api.redis).read_after("0-0", block_ms=None)
    assert [(e["event"], e["state_version"]) for e in events] == [
        ("incident.opened", 1),
        ("evidence.added", 2),
    ]
    WsServerMessage.model_validate(events[1])
    assert await live_api.pool.fetchval("select state_version from ocp.incidents") == 2
    assert (await counts(live_api))["jobs"] == 1


async def test_other_services_and_resolved_incidents_open_new_ones(live_api: LiveApi) -> None:
    api = (await live_api.client.post("/ingest/alert", json=alert(), headers=HEADERS)).json()
    worker = await live_api.client.post(
        "/ingest/alert", json=alert("worker", name="job_failure_rate", value=0.4), headers=HEADERS
    )
    assert worker.json()["deduplicated"] is False
    await live_api.pool.execute(
        "update ocp.incidents set status = 'resolved', resolved_at = now(),"
        " resolution = 'manual', window_end = now() where id = $1",
        uuid.UUID(api["incident_id"]),
    )
    again = await live_api.client.post("/ingest/alert", json=alert(), headers=HEADERS)
    assert again.json()["deduplicated"] is False
    assert again.json()["incident_id"] != api["incident_id"]


async def test_benchmark_run_is_stamped(live_api: LiveApi) -> None:
    await live_api.redis.set(BENCH_RUN_KEY, "run-07")
    await live_api.client.post("/ingest/alert", json=alert(), headers=HEADERS)
    assert await live_api.pool.fetchval("select benchmark_run_id from ocp.incidents") == "run-07"


async def test_manual_baseline_enqueues_no_agent_job(pool: Any, redis_client: Any) -> None:
    manual = IngestConfig(SecretStr(INGEST_TOKEN), False, b"detector: test\n")
    async with serve(build_services(pool, SigningKey("k"), redis_client, manual)) as client:
        response = await client.post("/ingest/alert", json=alert(), headers=HEADERS)
    assert response.status_code == 202
    assert await redis_client.xlen(AGENT_JOBS_STREAM) == 0
    assert await redis_client.xlen("ocp:events") == 1
