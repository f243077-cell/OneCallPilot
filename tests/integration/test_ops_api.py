"""Manual resolve, log tail, audit and settings (B3.2; FR-012, FR-023, API-006, API-007,
API-012, API-013, SEC-008)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest
from approval_env import make_pending
from conftest import TEST_INGEST, build_services, serve
from oncallpilot_contracts.domain import MonitorSettings
from redis.asyncio import Redis
from test_agent_db import open_incident

from oncallpilot.agent.tools.clients import LokiClient
from oncallpilot.db import approvals as approvals_db
from oncallpilot.detector.config import read_config
from tests.keys import SigningKey
from tests.test_detector_config import CONFIG

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
INGEST = replace(TEST_INGEST, detector_config=read_config(CONFIG))
PLANTED = "hunter2-planted-secret"


@dataclass
class Ops:
    client: httpx.AsyncClient
    pool: asyncpg.Pool
    key: SigningKey
    clock: list[datetime]
    loki_queries: list[dict[str, str]]
    user: str = "7e3a1c9b-2d5f-4b8e-a6c0-4f1d8b2e7a95"

    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key.token(sub=self.user)}"}


def loki(queries: list[dict[str, str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(dict(request.url.params))
        ns = int((NOW - timedelta(seconds=5)).timestamp() * 1e9)
        lines = [
            [str(ns), json.dumps({"level": "ERROR", "msg": f"login password={PLANTED} failed",
                                  "exc_type": "AuthError"})],
            [str(ns - 10**9), json.dumps({"level": "INFO", "msg": "GET /products 200"})],
        ]  # fmt: skip
        return httpx.Response(
            200,
            json={"status": "success",
                  "data": {"result": [{"stream": {"level": "ERROR"}, "values": lines}]}},
        )  # fmt: skip

    return httpx.MockTransport(handler)


@pytest.fixture
async def ops(pool: asyncpg.Pool, redis_client: Redis) -> AsyncIterator[Ops]:
    key = SigningKey("it-key")
    clock = [NOW]
    queries: list[dict[str, str]] = []
    async with httpx.AsyncClient(transport=loki(queries)) as http:
        services = replace(
            build_services(pool, key, redis_client, INGEST),
            clock=lambda: clock[0],
            loki=LokiClient(http, "http://loki:3100"),
        )
        async with serve(services) as client:
            yield Ops(client, pool, key, clock, queries)


# --- resolve (FR-012) ---------------------------------------------------------------


async def test_manual_resolve_closes_the_pending_proposal(ops: Ops) -> None:
    incident_id = await open_incident(ops.pool)
    proposal_id = await make_pending(ops.pool, incident_id)
    response = await ops.client.post(
        f"/incidents/{incident_id}/resolve", json={"reason": "fixed by hand"}, headers=ops.auth()
    )
    assert response.status_code == 200, response.text
    assert (response.json()["status"], response.json()["resolution"]) == ("resolved", "manual")
    assert response.json()["pending_proposal_id"] is None
    status = await ops.pool.fetchval(
        "select status::text from ocp.proposals where id = $1", proposal_id
    )
    assert status == "expired"
    audit = await ops.pool.fetchrow(
        "select actor, payload from ocp.audit_log where event = 'incident.resolved'"
    )
    assert audit is not None and audit["actor"] == f"user:{ops.user}"
    assert audit["payload"]["reason"] == "fixed by hand"
    again = await ops.client.post(
        f"/incidents/{incident_id}/resolve", json={"reason": "again"}, headers=ops.auth()
    )
    assert again.status_code == 200  # safe to repeat


async def test_resolve_is_refused_while_an_execution_runs(ops: Ops) -> None:
    incident_id = await open_incident(ops.pool)
    proposal_id = await make_pending(ops.pool, incident_id)
    async with ops.pool.acquire() as conn:
        approval = await approvals_db.insert_approval(
            conn, proposal_id=proposal_id, user_id=uuid.UUID(ops.user), decision="approved",
            reason=None, auth_method="biometric", challenge_id=uuid.uuid4(), device_id=None,
            idempotency_key=uuid.uuid4(), now=NOW,
        )  # fmt: skip
        await approvals_db.insert_execution(conn, proposal_id, approval, NOW)
    busy = await ops.client.post(
        f"/incidents/{incident_id}/resolve", json={"reason": "fixed"}, headers=ops.auth()
    )
    assert (busy.status_code, busy.json()["error"]["code"]) == (409, "INCIDENT_BUSY")


async def test_resolve_errors(ops: Ops) -> None:
    missing = await ops.client.post(
        f"/incidents/{uuid.uuid4()}/resolve", json={"reason": "fixed"}, headers=ops.auth()
    )
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")
    short = await ops.client.post(
        f"/incidents/{uuid.uuid4()}/resolve", json={"reason": "no"}, headers=ops.auth()
    )
    assert short.status_code == 422


# --- log tail (API-006, SEC-008) ------------------------------------------------------


async def test_log_tail_is_redacted_ordered_and_rate_limited(ops: Ops) -> None:
    incident_id = await open_incident(ops.pool)
    response = await ops.client.get(f"/incidents/{incident_id}/log-tail", headers=ops.auth())
    assert response.status_code == 200, response.text
    body = response.json()
    assert PLANTED not in response.text and "[REDACTED:secret]" in response.text
    assert [line["level"] for line in body["lines"]] == ["ERROR", "ERROR"]
    assert body["lines"][0]["msg"] == "GET /products 200"  # oldest first
    assert body["lines"][1]["exc_type"] == "AuthError"
    assert body["until"] == "2026-10-21T12:00:00Z"
    query = ops.loki_queries[0]
    assert query["query"] == '{service="api"}'
    assert int(query["start"]) == int((NOW - timedelta(seconds=60)).timestamp() * 1e9)

    limited = await ops.client.get(f"/incidents/{incident_id}/log-tail", headers=ops.auth())
    assert (limited.status_code, limited.json()["error"]["code"]) == (429, "RATE_LIMITED")
    assert limited.json()["error"]["details"]["retry_after"] == 2

    ops.clock[0] += timedelta(seconds=2)
    old = (NOW - timedelta(hours=1)).isoformat()
    later = await ops.client.get(
        f"/incidents/{incident_id}/log-tail", params={"since": old}, headers=ops.auth()
    )
    assert later.status_code == 200
    # Never further back than 10 minutes.
    start = int(ops.loki_queries[1]["start"])
    assert start == int((ops.clock[0] - timedelta(minutes=10)).timestamp() * 1e9)


async def test_log_tail_for_an_unknown_incident(ops: Ops) -> None:
    response = await ops.client.get(f"/incidents/{uuid.uuid4()}/log-tail", headers=ops.auth())
    assert response.status_code == 404


# --- audit (API-012) ------------------------------------------------------------------


async def test_audit_pages_newest_first_and_filters(ops: Ops) -> None:
    first = await open_incident(ops.pool)
    await ops.pool.execute(
        "update ocp.incidents set status = 'resolved', resolved_at = now(),"
        " resolution = 'manual', window_end = now() where id = $1",
        first,
    )
    second = await open_incident(ops.pool)
    await make_pending(ops.pool, second)
    for _ in range(3):
        await ops.pool.execute(
            "insert into ocp.audit_log (actor, event, incident_id, payload)"
            " values ('system', 'health.verified', $1, '{}')",
            second,
        )
    seen: list[int] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 2, "incident_id": str(second)}
        if cursor:
            params["cursor"] = cursor
        page = (await ops.client.get("/audit", params=params, headers=ops.auth())).json()
        seen += [item["id"] for item in page["items"]]
        assert all(item["incident_id"] == str(second) for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(seen, reverse=True) and len(seen) == len(set(seen)) == 4
    bad = await ops.client.get("/audit", params={"cursor": "nope"}, headers=ops.auth())
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "VALIDATION_ERROR")


# --- settings (FR-023, API-013) -------------------------------------------------------


def update(version: int, **overrides: Any) -> dict[str, Any]:
    return {
        "version": version,
        "services": ["api", "worker"],
        "thresholds": {"error_rate.z": 5.0},
        "notify": {"min_push_severity": "sev2"},
    } | overrides


async def test_settings_read_update_and_version_conflict(ops: Ops) -> None:
    initial = MonitorSettings.model_validate(
        (await ops.client.get("/settings", headers=ops.auth())).json()
    )
    assert (initial.version, initial.locked, initial.thresholds) == (1, False, {})
    saved = await ops.client.put("/settings", json=update(1), headers=ops.auth())
    assert saved.status_code == 200, saved.text
    assert (saved.json()["version"], saved.json()["thresholds"]) == (2, {"error_rate.z": 5.0})
    stale = await ops.client.put("/settings", json=update(1), headers=ops.auth())
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "VERSION_CONFLICT")
    typo = await ops.client.put(
        "/settings", json=update(2, thresholds={"eror_rate.z": 5.0}), headers=ops.auth()
    )
    assert (typo.status_code, typo.json()["error"]["code"]) == (422, "VALIDATION_ERROR")
    audit = await ops.pool.fetchrow(
        "select actor, payload from ocp.audit_log where event = 'settings.updated'"
    )
    assert audit is not None and audit["payload"]["to_version"] == 2


async def test_settings_are_locked_during_the_benchmark(
    pool: asyncpg.Pool, redis_client: Redis
) -> None:
    key = SigningKey("it-key")
    services = build_services(pool, key, redis_client, replace(INGEST, benchmark_lock=True))
    async with serve(services) as client:
        headers = {"Authorization": f"Bearer {key.token()}"}
        current = (await client.get("/settings", headers=headers)).json()
        assert current["locked"] is True and current["lock_reason"]
        locked = await client.put("/settings", json=update(1), headers=headers)
        assert (locked.status_code, locked.json()["error"]["code"]) == (423, "BENCHMARK_LOCKED")
