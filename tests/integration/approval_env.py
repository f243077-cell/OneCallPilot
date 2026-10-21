"""Shared approval fixtures: a pending proposal on an open incident and backend-api with
the real ``Approvals`` service (used by test_approvals and test_outcomes)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest
from conftest import TEST_INGEST, build_services, serve
from redis.asyncio import Redis
from test_agent_db import open_incident

from oncallpilot.approvals.service import Approvals
from oncallpilot.db import proposals as proposals_db
from oncallpilot.events import EventStream
from tests.keys import SigningKey
from tests.runner_replies import ACTION_KEY, dry_run

NOW = datetime(2026, 10, 21, 10, 10, tzinfo=UTC)
FINGERPRINT = "a" * 64


@dataclass
class Env:
    client: httpx.AsyncClient
    key: SigningKey
    pool: asyncpg.Pool
    redis: Redis
    user: str
    incident_id: uuid.UUID
    proposal_id: uuid.UUID
    clock: list[datetime]
    approvals: Approvals

    def auth(self, user: str | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key.token(sub=user or self.user)}"}

    async def challenge(
        self, fingerprint: str = FINGERPRINT, user: str | None = None
    ) -> httpx.Response:
        return await self.client.post(
            f"/proposals/{self.proposal_id}/challenge",
            json={"proposal_fingerprint": fingerprint},
            headers=self.auth(user),
        )

    async def approve(
        self,
        body: dict[str, Any] | None = None,
        key: str | None = None,
        user: str | None = None,
        **overrides: Any,
    ) -> httpx.Response:
        if body is None:
            issued = (await self.challenge(user=user)).json()
            body = {
                "challenge_id": issued["challenge_id"],
                "nonce": issued["nonce"],
                "proposal_fingerprint": FINGERPRINT,
                "auth_method": "biometric",
            } | overrides
        headers = self.auth(user) | ({"Idempotency-Key": key} if key else {})
        return await self.client.post(
            f"/proposals/{self.proposal_id}/approve", json=body, headers=headers
        )

    async def reject(self, reason: str = "not this one", key: str | None = None) -> httpx.Response:
        headers = self.auth() | {"Idempotency-Key": key or str(uuid.uuid4())}
        return await self.client.post(
            f"/proposals/{self.proposal_id}/reject", json={"reason": reason}, headers=headers
        )


async def make_pending(
    pool: asyncpg.Pool, incident_id: uuid.UUID, risk_tier: str = "medium"
) -> uuid.UUID:
    proposal_id = uuid.uuid4()
    async with pool.acquire() as conn:
        evidence = await conn.fetchval(
            "select id from ocp.evidence where incident_id = $1 and ref = 'E1'", incident_id
        )
        await proposals_db.insert_pending(
            conn,
            proposal_id=proposal_id,
            incident_id=incident_id,
            hypothesis_id=None,
            action="rollback_deploy" if risk_tier == "medium" else "restart_service",
            params={"service": "api", "target_release": "1.4.0"}
            if risk_tier == "medium"
            else {"service": "api"},
            risk_tier=risk_tier,
            approval_requirement="biometric" if risk_tier == "medium" else "tap",
            expected_effect="errors stop",
            rationale="errors began with the deploy",
            evidence_ids=[evidence],
            rollback_step=None,
            dry_run_result=dry_run(),
            dry_run_at=NOW - timedelta(seconds=5),
            fingerprint=FINGERPRINT,
            expires_at=NOW + timedelta(seconds=900),
            catalogue_version="1.0.0",
            now=NOW,
        )
        await conn.execute(
            "update ocp.incidents set status = 'awaiting_approval' where id = $1", incident_id
        )
    return proposal_id


@pytest.fixture
async def env(pool: asyncpg.Pool, redis_client: Redis) -> AsyncIterator[Env]:
    incident_id = await open_incident(pool)
    proposal_id = await make_pending(pool, incident_id)
    clock = [NOW]
    key = SigningKey("it-key")
    services = build_services(pool, key, redis_client, TEST_INGEST)
    approvals = Approvals(
        pool=pool,
        redis=redis_client,
        events=EventStream(redis_client),
        action_signing_key=ACTION_KEY,
        clock=lambda: clock[0],
        idempotency_wait_seconds=2.0,
    )
    services = replace(services, clock=lambda: clock[0], approvals=approvals)
    async with serve(services) as client:
        yield Env(
            client,
            key,
            pool,
            redis_client,
            str(uuid.uuid4()),
            incident_id,
            proposal_id,
            clock,
            approvals,
        )
