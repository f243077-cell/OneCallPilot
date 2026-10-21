"""Device registration and pushes with an FCM fake (B2.11; FR-018, API-011, SEC-009)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import asyncpg
from conftest import LiveApi
from redis.asyncio import Redis
from test_agent_db import open_incident

from oncallpilot.escalation import escalate_incident
from oncallpilot.events import EventStream
from oncallpilot.push.fcm import FakePushTransport
from oncallpilot.push.notifier import Notifier

NOW = datetime(2026, 10, 21, 11, 0, tzinfo=UTC)


async def register(live_api: LiveApi, user: str, token: str) -> str:
    response = await live_api.client.post(
        "/devices",
        json={"fcm_token": token, "platform": "android", "app_version": "0.1.0"},
        headers=live_api.auth(user),
    )
    assert response.status_code == 200, response.text
    return str(response.json()["device_id"])


async def test_register_is_an_upsert_by_token_and_delete_is_owner_only(
    live_api: LiveApi,
) -> None:
    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    first = await register(live_api, alice, "token-1")
    assert await register(live_api, alice, "token-1") == first
    # The same phone signs in as someone else: the token moves to them.
    assert await register(live_api, bob, "token-1") == first
    other = await live_api.client.delete(f"/devices/{first}", headers=live_api.auth(alice))
    assert other.status_code == 404
    for _ in range(2):  # safe to repeat
        gone = await live_api.client.delete(f"/devices/{first}", headers=live_api.auth(bob))
        assert gone.status_code == 204
    row = await live_api.pool.fetchrow("select user_id, push_enabled from ocp.devices")
    assert row is not None and (str(row["user_id"]), row["push_enabled"]) == (bob, False)
    audit = await live_api.pool.fetch(
        "select payload from ocp.audit_log where event = 'device.registered'"
    )
    assert len(audit) == 3 and all("token-1" not in json.dumps(a["payload"]) for a in audit)
    # Registering again turns pushes back on.
    await register(live_api, bob, "token-1")
    assert await live_api.pool.fetchval("select push_enabled from ocp.devices") is True


async def add_device(pool: asyncpg.Pool, token: str, enabled: bool = True) -> None:
    await pool.execute(
        "insert into ocp.devices (user_id, fcm_token, platform, push_enabled)"
        " values ($1, $2, 'android', $3)",
        uuid.uuid4(),
        token,
        enabled,
    )


async def test_one_push_per_enabled_device_with_only_the_allowed_keys(
    pool: asyncpg.Pool, redis_client: Redis
) -> None:
    incident_id = await open_incident(pool)
    await add_device(pool, "live-1")
    await add_device(pool, "live-2")
    await add_device(pool, "off", enabled=False)
    await add_device(pool, "dead")
    fake = FakePushTransport(unregistered={"dead"})
    notifier = Notifier(pool, fake)
    events = EventStream(redis_client, on_published=notifier.on_event)

    assert await escalate_incident(pool, events, incident_id, "low_confidence", NOW)
    await notifier.drain()

    assert sorted(m["message"]["token"] for m in fake.sent) == ["live-1", "live-2"]
    for sent in fake.sent:
        data = sent["message"]["data"]
        assert set(data) == {"type", "incident_id", "severity", "service", "title"}
        assert data["type"] == "incident.escalated" and data["incident_id"] == str(incident_id)
        assert sent["message"]["notification"]["body"] == "Needs human investigation"
    # FR-018: the unregistered token no longer receives pushes.
    assert (
        await pool.fetchval("select push_enabled from ocp.devices where fcm_token = 'dead'")
        is False
    )


async def test_min_push_severity_filters(pool: asyncpg.Pool) -> None:
    incident_id = await open_incident(pool)
    await pool.execute("update ocp.incidents set severity = 'sev3' where id = $1", incident_id)
    await pool.execute(
        "insert into ocp.monitor_settings (id, version, settings) values (1, 1, $1)",
        {"services": ["api"], "thresholds": {}, "notify": {"min_push_severity": "sev2"}},
    )
    await add_device(pool, "live")
    fake = FakePushTransport()
    assert await Notifier(pool, fake).send("incident.opened", incident_id) == 0
    await pool.execute("update ocp.incidents set severity = 'sev2' where id = $1", incident_id)
    assert await Notifier(pool, fake).send("incident.opened", incident_id) == 1


async def test_events_that_do_not_push(pool: asyncpg.Pool) -> None:
    incident_id = await open_incident(pool)
    await add_device(pool, "live")
    fake = FakePushTransport()
    notifier = Notifier(pool, fake)
    notifier.on_event("evidence.added", incident_id, {})
    notifier.on_event("incident.updated", incident_id, {"status": "verifying"})
    notifier.on_event("incident.updated", incident_id, {"status": "action_failed"})
    await notifier.drain()
    assert [m["message"]["data"]["type"] for m in fake.sent] == ["incident.action_failed"]
