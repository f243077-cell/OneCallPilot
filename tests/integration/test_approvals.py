"""Challenge, approve, reject against Postgres and Redis (B2.9; C8; API-008…API-010,
SEC-001, SEC-002, SEC-012, SEC-017, TEST-005, TEST-006)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from approval_env import FINGERPRINT, NOW, Env, make_pending
from fake_runner import fake_runner
from oncallpilot_contracts.runner import REQUESTS_STREAM, STREAM_FIELD, RunnerRequest
from oncallpilot_contracts.signing import verify
from oncallpilot_contracts.ws import WsServerMessage
from redis.asyncio import Redis

from oncallpilot.approvals import limits
from oncallpilot.events import EventStream
from tests.runner_replies import ACTION_KEY, LINK_KEY, STATE_FINGERPRINT


def code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


async def execute_requests(redis: Redis) -> list[dict[str, Any]]:
    entries: Any = await redis.xrange(REQUESTS_STREAM)
    return [json.loads(fields[STREAM_FIELD]) for _, fields in entries]


# --- challenge (API-008, SEC-012) ---------------------------------------------------------


async def test_challenge_is_single_use_and_stores_only_the_nonce_hash(env: Env) -> None:
    response = await env.challenge()
    assert response.status_code == 200
    body = response.json()
    assert body["approval_requirement"] == "biometric"
    assert len(body["nonce"]) == 64
    assert body["expires_at"] == "2026-10-21T10:12:00Z"
    stored = await env.redis.get(f"ocp:challenge:{body['challenge_id']}")
    assert stored is not None and body["nonce"] not in stored
    assert 0 < await env.redis.ttl(f"ocp:challenge:{body['challenge_id']}") <= 120


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("stale", (409, "STALE_PROPOSAL")),
        ("expired", (409, "PROPOSAL_EXPIRED")),
        ("decided", (409, "PROPOSAL_NOT_PENDING")),
        ("missing", (404, "NOT_FOUND")),
        ("no_token", (401, "UNAUTHORIZED")),
    ],
)
async def test_challenge_checks(env: Env, setup: str, expected: tuple[int, str]) -> None:
    fingerprint = FINGERPRINT
    if setup == "stale":
        fingerprint = "b" * 64
    elif setup == "expired":
        env.clock[0] = NOW + timedelta(seconds=901)
    elif setup == "decided":
        assert (await env.reject()).status_code == 200
    elif setup == "missing":
        env.proposal_id = uuid.uuid4()
    if setup == "no_token":
        response = await env.client.post(
            f"/proposals/{env.proposal_id}/challenge", json={"proposal_fingerprint": fingerprint}
        )
    else:
        response = await env.challenge(fingerprint)
    assert (response.status_code, code(response)) == expected


# --- approve (API-009, SEC-001, SEC-002, SEC-017) ----------------------------------------


async def test_approve_creates_one_execution_and_sends_one_signed_execute(env: Env) -> None:
    key = str(uuid.uuid4())
    response = await env.approve(key=key, device_id=str(uuid.uuid4()))
    assert response.status_code == 202, response.text
    execution_id = response.json()["execution_id"]
    assert response.json()["status"] == "queued"

    row = await env.pool.fetchrow(
        "select e.status::text as e, p.status::text as p, i.status::text as i,"
        " a.decision::text as d, a.auth_method, a.device_id, a.idempotency_key"
        " from ocp.executions e join ocp.proposals p on p.id = e.proposal_id"
        " join ocp.incidents i on i.id = p.incident_id join ocp.approvals a on a.id = e.approval_id"
        " where e.id = $1",
        uuid.UUID(execution_id),
    )
    assert row is not None
    assert (row["e"], row["p"], row["i"], row["d"]) == (
        "queued",
        "approved",
        "executing",
        "approved",
    )
    assert row["device_id"] is None  # an unregistered device is not linked
    assert row["idempotency_key"] == key

    # SEC-002: one execute, signed with ACTION_SIGNING_KEY only, expiring 60 s after approval.
    [message] = await execute_requests(env.redis)
    assert verify(message, ACTION_KEY) and not verify(message, LINK_KEY)
    request = RunnerRequest.model_validate(message)
    assert request.type == "execute" and str(request.execution_id) == execution_id
    assert str(request.approved_by) == env.user and str(request.idempotency_key) == key
    assert request.state_fingerprint == STATE_FINGERPRINT
    assert request.expires_at - request.issued_at == timedelta(seconds=60)

    # SEC-017: the audit snapshot of what the engineer saw.
    audit = await env.pool.fetchrow(
        "select actor, payload from ocp.audit_log where event = 'approval.approved'"
    )
    assert audit is not None and audit["actor"] == f"user:{env.user}"
    assert {
        "fingerprint",
        "dry_run_result",
        "top_hypothesis",
        "evidence_refs",
        "auth_method",
        "device_id",
    } <= set(audit["payload"])
    assert audit["payload"]["evidence_refs"] == ["E1"]
    events = [
        r["event"] for r in await env.pool.fetch("select event from ocp.audit_log order by id")
    ]
    assert events[-4:] == [
        "challenge.issued",
        "approval.approved",
        "execution.queued",
        "incident.status_changed",
    ]

    published = await EventStream(env.redis).read_after("0-0", block_ms=None, count=10)
    for event in published:
        WsServerMessage.model_validate(event)
    assert [e["event"] for e in published] == ["proposal.updated", "incident.updated"]
    assert published[1]["state_version"] == published[0]["state_version"] + 1
    assert await env.redis.zcard(limits.ratelimit_key("api")) == 1


async def test_low_tier_accepts_a_tap(env: Env) -> None:
    env.proposal_id = await make_pending_after_reset(env, "low")
    response = await env.approve(key=str(uuid.uuid4()), auth_method="tap")
    assert response.status_code == 202, response.text


async def make_pending_after_reset(env: Env, tier: str) -> uuid.UUID:
    await env.pool.execute(
        "update ocp.proposals set status = 'superseded' where id = $1", env.proposal_id
    )
    return await make_pending(env.pool, env.incident_id, tier)


async def test_missing_or_malformed_key(env: Env) -> None:
    missing = await env.approve()
    assert (missing.status_code, code(missing)) == (400, "IDEMPOTENCY_KEY_REQUIRED")
    malformed = await env.approve(key="not-a-uuid")
    assert (malformed.status_code, code(malformed)) == (400, "IDEMPOTENCY_KEY_REQUIRED")


async def test_unknown_proposal_is_404_before_the_key(env: Env) -> None:
    env.proposal_id = uuid.uuid4()
    response = await env.approve(body={})
    assert (response.status_code, code(response)) == (404, "NOT_FOUND")


async def test_challenge_must_be_unused_and_for_this_user(env: Env) -> None:
    issued = (await env.challenge()).json()
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "biometric",
    }
    other = await env.approve(body=body, key=str(uuid.uuid4()), user=str(uuid.uuid4()))
    assert (other.status_code, code(other)) == (409, "CHALLENGE_INVALID")
    # Consumed by the failed attempt: the owner must start again too.
    reused = await env.approve(body=body, key=str(uuid.uuid4()))
    assert (reused.status_code, code(reused)) == (409, "CHALLENGE_INVALID")


async def test_wrong_nonce_is_challenge_invalid(env: Env) -> None:
    response = await env.approve(key=str(uuid.uuid4()), nonce="0" * 64)
    assert (response.status_code, code(response)) == (409, "CHALLENGE_INVALID")


async def test_stale_fingerprint_is_refused(env: Env) -> None:
    # TEST-006: the proposal changed after the challenge was issued.
    issued = (await env.challenge()).json()
    await env.pool.execute(
        "update ocp.proposals set fingerprint = $2 where id = $1", env.proposal_id, "c" * 64
    )
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "biometric",
    }
    response = await env.approve(body=body, key=str(uuid.uuid4()))
    assert (response.status_code, code(response)) == (409, "STALE_PROPOSAL")
    assert await execute_requests(env.redis) == []


async def test_expired_proposal_is_refused(env: Env) -> None:
    issued = (await env.challenge()).json()
    env.clock[0] = NOW + timedelta(seconds=901)
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "biometric",
    }
    response = await env.approve(body=body, key=str(uuid.uuid4()))
    assert (response.status_code, code(response)) == (409, "PROPOSAL_EXPIRED")


async def test_medium_tier_needs_biometric(env: Env) -> None:
    response = await env.approve(key=str(uuid.uuid4()), auth_method="tap")
    assert (response.status_code, code(response)) == (403, "BIOMETRIC_REQUIRED")
    assert await env.pool.fetchval("select count(*) from ocp.approvals") == 0


async def test_rate_limit_and_cooldown_report_retry_after(env: Env) -> None:
    for i in range(3):
        await env.redis.zadd(
            limits.ratelimit_key("api"), {f"x{i}": (NOW - timedelta(seconds=600 - i)).timestamp()}
        )
    limited = await env.approve(key=str(uuid.uuid4()))
    assert (limited.status_code, code(limited)) == (429, "RATE_LIMITED")
    assert limited.json()["error"]["details"]["retry_after"] == 1200

    await env.redis.delete(limits.ratelimit_key("api"))
    await env.redis.set(limits.cooldown_key("api"), "1", ex=90)
    cooling = await env.approve(key=str(uuid.uuid4()))
    assert (cooling.status_code, code(cooling)) == (429, "COOLDOWN_ACTIVE")
    assert 0 < cooling.json()["error"]["details"]["retry_after"] <= 90


async def test_rollback_proposals_are_exempt_from_the_cooldown(env: Env) -> None:
    await env.redis.set(limits.cooldown_key("api"), "1", ex=90)
    async with env.pool.acquire() as conn:
        parent = env.proposal_id
        await conn.execute("update ocp.proposals set status = 'failed' where id = $1", parent)
        env.proposal_id = await make_pending(env.pool, env.incident_id)
        await conn.execute(
            "update ocp.proposals set kind = 'rollback', parent_proposal_id = $2 where id = $1",
            env.proposal_id,
            parent,
        )
    response = await env.approve(key=str(uuid.uuid4()))
    assert response.status_code == 202, response.text


# --- idempotency (TEST-005) --------------------------------------------------------------


async def test_replayed_key_returns_the_same_response_and_one_execution(env: Env) -> None:
    key = str(uuid.uuid4())
    issued = (await env.challenge()).json()
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "biometric",
    }
    first, second = await asyncio.gather(
        env.approve(body=body, key=key), env.approve(body=body, key=key)
    )
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    third = await env.approve(body=body, key=key)
    assert third.json() == first.json()
    assert await env.pool.fetchval("select count(*) from ocp.executions") == 1
    assert len(await execute_requests(env.redis)) == 1


async def test_same_key_with_another_body_is_a_conflict(env: Env) -> None:
    key = str(uuid.uuid4())
    assert (await env.approve(key=key, auth_method="tap")).status_code == 403
    other = await env.approve(key=key)
    assert (other.status_code, code(other)) == (422, "IDEMPOTENCY_CONFLICT")


async def test_error_responses_are_replayed_too(env: Env) -> None:
    key = str(uuid.uuid4())
    issued = (await env.challenge()).json()
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "tap",
    }
    first = await env.approve(body=body, key=key)
    again = await env.approve(body=body, key=key)
    assert first.status_code == again.status_code == 403
    assert first.json() == again.json()


async def test_second_key_after_approval_is_not_pending(env: Env) -> None:
    early = (await env.challenge()).json()
    assert (await env.approve(key=str(uuid.uuid4()))).status_code == 202
    body = {
        "challenge_id": early["challenge_id"],
        "nonce": early["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "biometric",
    }
    late = await env.approve(body=body, key=str(uuid.uuid4()))
    assert (late.status_code, code(late)) == (409, "PROPOSAL_NOT_PENDING")
    assert await env.pool.fetchval("select count(*) from ocp.executions") == 1


async def test_redis_down_refuses(env: Env, redis_client: Redis) -> None:
    issued = (await env.challenge()).json()
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "biometric",
    }
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    env.approvals.redis = broken
    try:
        response = await env.approve(body=body, key=str(uuid.uuid4()))
    finally:
        env.approvals.redis = redis_client
        await broken.aclose()
    assert response.status_code == 500
    assert await env.pool.fetchval("select count(*) from ocp.executions") == 0


# --- reject (API-010) -------------------------------------------------------------------


async def test_reject_escalates_and_records_the_reason(env: Env) -> None:
    response = await env.reject("wrong service")
    assert response.status_code == 200
    assert response.json() == {"proposal_id": str(env.proposal_id), "status": "rejected"}
    row = await env.pool.fetchrow(
        "select i.status::text as i, i.status_reason, p.status::text as p, a.reason"
        " from ocp.proposals p join ocp.incidents i on i.id = p.incident_id"
        " join ocp.approvals a on a.proposal_id = p.id where p.id = $1",
        env.proposal_id,
    )
    assert row is not None
    assert (row["i"], row["status_reason"], row["p"], row["reason"]) == (
        "escalated",
        "proposal_rejected",
        "rejected",
        "wrong service",
    )
    assert await env.pool.fetchval("select count(*) from ocp.executions") == 0
    assert await execute_requests(env.redis) == []


async def test_reject_checks(env: Env) -> None:
    short = await env.reject("no")
    assert (short.status_code, code(short)) == (422, "VALIDATION_ERROR")
    key = str(uuid.uuid4())
    assert (await env.reject("first reason", key=key)).status_code == 200
    conflict = await env.reject("other reason", key=key)
    assert (conflict.status_code, code(conflict)) == (422, "IDEMPOTENCY_CONFLICT")
    no_key = await env.client.post(
        f"/proposals/{env.proposal_id}/reject", json={"reason": "abc"}, headers=env.auth()
    )
    assert (no_key.status_code, code(no_key)) == (400, "IDEMPOTENCY_KEY_REQUIRED")


async def test_reject_after_approve_is_not_pending(env: Env) -> None:
    assert (await env.approve(key=str(uuid.uuid4()))).status_code == 202
    response = await env.reject()
    assert (response.status_code, code(response)) == (409, "PROPOSAL_NOT_PENDING")


async def test_the_fake_runner_sees_a_valid_execute(env: Env) -> None:
    async with fake_runner(env.redis, lambda request: None) as runner:
        assert (await env.approve(key=str(uuid.uuid4()))).status_code == 202
        for _ in range(50):
            if runner.requests:
                break
            await asyncio.sleep(0.05)
    assert runner.bad_signatures == 0
    assert [r.type for r in runner.requests] == ["execute"]
