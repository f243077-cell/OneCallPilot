"""GET /healthz, /incidents and /incidents/{id} against Postgres (API-002, API-004, API-005)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import jsonschema
import pytest
from conftest import Api
from oncallpilot_contracts.domain import IncidentDetail
from oncallpilot_contracts.timeline import Timeline

from oncallpilot.db.seed import write_incident_detail

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
TIMELINES = sorted((CONTRACTS / "fixtures" / "timelines").glob("*.json"))
T0 = datetime(2026, 10, 21, 8, 0, tzinfo=UTC)


def schema_validator(name: str) -> jsonschema.Draft202012Validator:
    """Validator for one generated contract schema (they are self-contained)."""
    schema = json.loads((CONTRACTS / "schemas" / name).read_text())
    return jsonschema.Draft202012Validator(
        schema, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
    )


async def insert_incidents(pool: asyncpg.Pool, count: int, **cols: object) -> list[uuid.UUID]:
    ids = []
    for i in range(count):
        service = cols.get("service", ["api", "worker", "lb", "payments", "redis"][i % 5])
        status = cols.get("status", "resolved")
        resolved = status == "resolved"
        ids.append(
            await pool.fetchval(
                "insert into ocp.incidents (service, severity, status, title, trigger,"
                " opened_at, resolved_at, resolution, window_start, window_end,"
                " detector_config_version)"
                " values ($1, 'sev2', $2::text::ocp.incident_status, $3, $4, $5, $6,"
                " $7::text::ocp.resolution, $5, $6, '1') returning id",
                service,
                status,
                f"incident {i}",
                {},
                T0 + timedelta(minutes=i),
                T0 + timedelta(minutes=i, seconds=30) if resolved else None,
                "manual" if resolved else None,
            )
        )
    return ids


async def test_healthz_reports_the_database(api: Api) -> None:
    response = await api.client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "contracts_version": "0.1.0",
        "db": "up",
        "redis": "up",
    }
    api.redis.up = False
    response = await api.client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["redis"] == "down"


async def test_45_incidents_in_pages_of_20(api: Api, pool: asyncpg.Pool) -> None:
    ids = await insert_incidents(pool, 45)
    seen: list[str] = []
    pages = 0
    cursor = None
    validator = schema_validator("incident_page.json")
    while True:
        params: dict[str, str | int] = {"limit": 20}
        if cursor:
            params["cursor"] = cursor
        response = await api.client.get("/incidents", params=params, headers=api.auth())
        assert response.status_code == 200
        body = response.json()
        validator.validate(body)
        pages += 1
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert len(seen) == len(set(seen)) == 45
    # Newest first.
    assert seen == [str(i) for i in reversed(ids)]


async def test_page_boundary_has_no_extra_empty_page(api: Api, pool: asyncpg.Pool) -> None:
    await insert_incidents(pool, 20)
    response = await api.client.get("/incidents", params={"limit": 20}, headers=api.auth())
    body = response.json()
    assert len(body["items"]) == 20
    assert body["next_cursor"] is None


async def test_ties_on_opened_at_are_paged_by_id(api: Api, pool: asyncpg.Pool) -> None:
    for i in range(5):
        await pool.execute(
            "insert into ocp.incidents (service, severity, status, title, trigger, opened_at,"
            " resolved_at, resolution, window_start, window_end, detector_config_version)"
            " values ('api', 'sev3', 'resolved', $1, '{}', $2, $2, 'manual', $2, $2, '1')",
            f"tie {i}",
            T0,
        )
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = (await api.client.get("/incidents", params=params, headers=api.auth())).json()
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 5


async def test_filters(api: Api, pool: asyncpg.Pool) -> None:
    await insert_incidents(pool, 10)
    [active] = await insert_incidents(pool, 1, service="api", status="escalated")
    await pool.execute("update ocp.incidents set status_reason = 'low_confidence'")
    response = await api.client.get(
        "/incidents", params={"status": "escalated"}, headers=api.auth()
    )
    assert [item["id"] for item in response.json()["items"]] == [str(active)]
    response = await api.client.get("/incidents", params={"service": "worker"}, headers=api.auth())
    items = response.json()["items"]
    assert len(items) == 2
    assert {item["service"] for item in items} == {"worker"}


@pytest.mark.parametrize("path", TIMELINES, ids=lambda p: p.stem)
async def test_detail_returns_the_seeded_fixture(api: Api, pool: asyncpg.Pool, path: Path) -> None:
    final = Timeline.model_validate_json(path.read_text()).final
    async with pool.acquire() as conn:
        await write_incident_detail(conn, final)
    response = await api.client.get(f"/incidents/{final.id}", headers=api.auth())
    assert response.status_code == 200, response.text
    body = response.json()
    schema_validator("incident_detail.json").validate(body)
    assert IncidentDetail.model_validate(body) == final

    page = (await api.client.get("/incidents", headers=api.auth())).json()
    [summary] = page["items"]
    assert summary["id"] == str(final.id)
    assert summary["pending_proposal_id"] == (
        str(final.pending_proposal_id) if final.pending_proposal_id else None
    )
    top = summary["top_hypothesis"]
    active = sorted((h for h in final.hypotheses if h.status == "active"), key=lambda h: h.rank)
    assert (top["summary"] if top else None) == (active[0].summary if active else None)


async def test_seeding_writes_audit_rows(pool: asyncpg.Pool) -> None:
    final = Timeline.model_validate_json(TIMELINES[0].read_text()).final
    async with pool.acquire() as conn:
        await write_incident_detail(conn, final)
    events = await pool.fetch(
        "select event from ocp.audit_log where incident_id = $1 order by id", final.id
    )
    assert events[0]["event"] == "incident.opened"
    assert len(events) >= 1 + len(final.evidence)


async def test_unknown_incident_is_404(api: Api) -> None:
    response = await api.client.get(f"/incidents/{uuid.uuid4()}", headers=api.auth())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
