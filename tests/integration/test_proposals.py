"""Proposal validation, the signed dry run and the stored proposal, against Postgres
and Redis with a FakeRunnerConsumer (B2.8; architecture §5.6; AI-018, RUN-007, FR-010,
FR-019, SEC-003)."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from fake_runner import fake_runner
from oncallpilot_contracts.runner import RESULTS_STREAM, STREAM_FIELD, RunnerRequest
from oncallpilot_contracts.ws import WsServerMessage
from redis.asyncio import Redis
from test_agent_db import LEDGER, NOW, loki, open_incident, run, script, unused

from oncallpilot.agent.effects import DbEffects
from oncallpilot.agent.proposer import RunnerProposer, proposal_fingerprint
from oncallpilot.agent.providers.embeddings import FakeEmbeddingProvider
from oncallpilot.agent.toolbox import ToolBox
from oncallpilot.agent.tools.clients import LokiClient, PrometheusClient
from oncallpilot.db.incidents import IncidentRepository
from oncallpilot.events import EventStream
from oncallpilot.runner_link import RunnerLink
from tests.agent.fakes import proposal
from tests.runner_replies import ACTION_KEY, LINK_KEY, dry_run_reply
from tests.test_catalogue import ROOT, ledger


@pytest.fixture
async def effects(
    pool: asyncpg.Pool, redis_client: Redis, tmp_path: Path
) -> AsyncIterator[DbEffects]:
    incident_id = await open_incident(pool)
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(loki)) as logs,
        httpx.AsyncClient(transport=httpx.MockTransport(unused)) as other,
    ):
        yield DbEffects(
            pool=pool,
            events=EventStream(redis_client),
            tools=ToolBox(
                loki=LokiClient(logs, "http://loki:3100"),
                prometheus=PrometheusClient(other, "http://prometheus:9090"),
                docker=other,
                http=other,
                ledger=LEDGER,
                pool=pool,
                embedder=FakeEmbeddingProvider(),
            ),
            incident_id=incident_id,
            provider_name="fake",
            model="fake-1",
            proposer=RunnerProposer(
                catalogue_path=ROOT / "contracts" / "actions.yaml",
                targets_path=ROOT / "runner" / "targets.yaml",
                ledger_path=ledger(tmp_path, "1.4.0", "1.5.0"),
                link=RunnerLink(redis_client, LINK_KEY, clock=lambda: NOW, wait_seconds=0.5),
            ),
            clock=lambda: NOW,
        )


def with_proposal(**overrides: Any) -> dict[str, list[Any]]:
    return script() | {"ProposalOut": [proposal(refs=["E2", "E3"]) | overrides]}


async def statuses(pool: asyncpg.Pool, incident_id: uuid.UUID) -> tuple[str, str | None]:
    row = await pool.fetchrow(
        "select status::text, status_reason from ocp.incidents where id = $1", incident_id
    )
    assert row is not None
    return row[0], row[1]


async def audited(pool: asyncpg.Pool, incident_id: uuid.UUID, event: str) -> list[Any]:
    return [
        r["payload"]
        for r in await pool.fetch(
            "select payload from ocp.audit_log where incident_id = $1 and event = $2 order by id",
            incident_id,
            event,
        )
    ]


async def test_valid_proposal_is_stored_pending_after_a_signed_dry_run(
    effects: DbEffects, pool: asyncpg.Pool, redis_client: Redis
) -> None:
    async with fake_runner(redis_client) as runner:
        state, _ = await run(effects, script())
    assert state.phase == "PROPOSED"

    # SEC-003: one dry_run request, signed with RUNNER_LINK_KEY only, no approval fields.
    assert runner.bad_signatures == 0 and len(runner.requests) == 1
    request = runner.requests[0]
    assert (request.type, request.action) == ("dry_run", "rollback_deploy")
    assert request.params == {"service": "api", "target_release": "1.4.0"}
    assert request.approved_by is None and request.idempotency_key is None

    incident_id = effects.incident_id
    assert await statuses(pool, incident_id) == ("awaiting_approval", None)
    row = await pool.fetchrow("select * from ocp.proposals where incident_id = $1", incident_id)
    assert row is not None
    assert row["id"] == request.proposal_id
    assert (row["status"], row["risk_tier"], row["approval_requirement"]) == (
        "pending",
        "medium",
        "biometric",
    )
    assert (row["expires_at"] - NOW).total_seconds() == 900
    assert row["rollback_step"] == {
        "action": "rollback_deploy",
        "params": {"service": "api", "target_release": "1.5.0"},
    }
    assert row["fingerprint"] == proposal_fingerprint(
        row["id"],
        "rollback_deploy",
        row["params"],
        "medium",
        row["dry_run_result"]["state_fingerprint"],
        row["dry_run_at"],
    )
    top = await pool.fetchval(
        "select id from ocp.hypotheses where incident_id = $1 and status = 'active' and rank = 1",
        incident_id,
    )
    assert row["hypothesis_id"] == top

    # The REST detail serves it as a valid C2 Proposal with refs, not UUIDs.
    detail = await IncidentRepository(pool).get_detail(incident_id)
    assert detail is not None and len(detail.proposals) == 1
    assert detail.proposals[0].evidence_refs == ["E2", "E3"]
    assert detail.pending_proposal_id == row["id"]

    # FR-019: proposal.created then incident.updated, consecutive state versions.
    events = await EventStream(redis_client).read_after("0-0", block_ms=None, count=100)
    for event in events:
        WsServerMessage.model_validate(event)
    created, updated = events[-2:]
    assert (created["event"], updated["event"]) == ("proposal.created", "incident.updated")
    assert updated["state_version"] == created["state_version"] + 1
    assert created["data"]["fingerprint"] == row["fingerprint"]
    assert updated["data"]["status"] == "awaiting_approval"

    created_audit = await audited(pool, incident_id, "proposal.created")
    assert len(created_audit) == 1 and created_audit[0]["fingerprint"] == row["fingerprint"]
    changes = await audited(pool, incident_id, "incident.status_changed")
    assert changes[-1] == {"from": "investigating", "to": "awaiting_approval"}


async def test_invalid_proposal_escalates_and_is_audited_without_a_dry_run(
    effects: DbEffects, pool: asyncpg.Pool, redis_client: Redis
) -> None:
    bad = {"params": {"service": "api", "target_release": "9.9.9"}}
    async with fake_runner(redis_client) as runner:
        state, _ = await run(effects, with_proposal(**bad))
    assert (state.phase, state.escalation_reason) == ("ESCALATED", "proposal_invalid")
    assert runner.requests == []
    assert await statuses(pool, effects.incident_id) == ("escalated", "proposal_invalid")
    rejected = await audited(pool, effects.incident_id, "proposal.rejected_by_validator")
    assert len(rejected) == 1
    assert rejected[0]["action"] == "rollback_deploy" and rejected[0]["problems"]
    assert await pool.fetchval("select count(*) from ocp.proposals") == 0


async def test_silent_runner_is_retried_once_then_unavailable(
    effects: DbEffects, pool: asyncpg.Pool, redis_client: Redis
) -> None:
    async with fake_runner(redis_client, lambda request: None) as runner:
        state, _ = await run(effects, script())
    assert (state.phase, state.escalation_reason) == ("ESCALATED", "dry_run_unavailable")
    assert len(runner.requests) == 2
    assert runner.requests[0].request_id != runner.requests[1].request_id
    assert await pool.fetchval("select count(*) from ocp.proposals") == 0


@pytest.mark.parametrize(
    ("code", "reason"),
    [("TARGET_NOT_ALLOWED", "proposal_invalid"), ("COOLDOWN_ACTIVE", "dry_run_unavailable")],
)
async def test_runner_refusal_escalates(
    effects: DbEffects, pool: asyncpg.Pool, redis_client: Redis, code: str, reason: str
) -> None:
    def refuse(request: RunnerRequest) -> dict[str, Any]:
        return dry_run_reply(str(request.request_id), error_code=code)

    async with fake_runner(redis_client, refuse):
        state, _ = await run(effects, script())
    assert (state.phase, state.escalation_reason) == ("ESCALATED", reason)
    assert await statuses(pool, effects.incident_id) == ("escalated", reason)


async def test_forged_or_foreign_results_are_ignored(
    effects: DbEffects, pool: asyncpg.Pool, redis_client: Redis
) -> None:
    def forged(request: RunnerRequest) -> dict[str, Any]:
        # Signed with the wrong key: the worker must not trust it (SEC-003).
        return dry_run_reply(str(request.request_id), key=ACTION_KEY)

    await redis_client.xadd(RESULTS_STREAM, {STREAM_FIELD: "not json"})
    await redis_client.xadd(
        RESULTS_STREAM, {STREAM_FIELD: json.dumps(dry_run_reply(str(uuid.uuid4())))}
    )
    async with fake_runner(redis_client, forged):
        state, _ = await run(effects, script())
    assert (state.phase, state.escalation_reason) == ("ESCALATED", "dry_run_unavailable")
    assert await pool.fetchval("select count(*) from ocp.proposals") == 0
