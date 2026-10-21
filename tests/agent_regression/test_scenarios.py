"""Agent regression suite with FakeProvider (TEST-002; AI-001…AI-020).

For every scenario file: the terminal state, the proposal's action and
parameters (or the escalation reason), budget compliance, and citation
validity, all read back from the database the real pipeline wrote.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import pytest
from redis.asyncio import Redis
from replay import SCENARIOS, Outcome, Scenario, load, run

FILES = sorted(SCENARIOS.glob("*.yaml"))


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
async def test_scenario(
    path: Path, pool: asyncpg.Pool, redis_client: Redis, tmp_path: Path
) -> None:
    scenario = load(path)
    outcome = await run(scenario, pool, redis_client, tmp_path)
    expect = scenario.data["expect"]

    incident = await pool.fetchrow(
        "select status::text as status, status_reason, agent_meta from ocp.incidents where id = $1",
        outcome.incident_id,
    )
    assert incident is not None
    assert outcome.state.phase == expect["phase"], outcome.state
    if expect["phase"] == "PROPOSED":
        assert incident["status"] == "awaiting_approval"
        await check_proposal(pool, outcome, expect)
    else:
        assert (incident["status"], incident["status_reason"]) == (
            "escalated",
            expect["escalation_reason"],
        )
        assert await pool.fetchval("select count(*) from ocp.proposals") == 0
        assert outcome.runner_requests == []  # nothing reached the runner

    await check_budgets(incident["agent_meta"])
    await check_citations(pool, outcome)
    await check_flags(pool, outcome, scenario)


async def check_proposal(pool: asyncpg.Pool, outcome: Outcome, expect: dict[str, object]) -> None:
    row = await pool.fetchrow(
        "select action, params, risk_tier::text as tier, status::text as status"
        " from ocp.proposals where incident_id = $1",
        outcome.incident_id,
    )
    assert row is not None
    assert (row["action"], row["params"], row["status"]) == (
        expect["action"],
        expect["params"],
        "pending",
    )
    # Exactly one signed dry run, for the same action and parameters.
    [request] = outcome.runner_requests
    assert (request.type, request.action, request.params) == (
        "dry_run",
        expect["action"],
        expect["params"],
    )


async def check_budgets(meta: dict[str, Any]) -> None:
    # CLAUDE.md §5.2: ≤ 8 tool calls (≤ 6 gather, 1 disproof, ≤ 1 propose), ≤ 15 LLM calls.
    assert meta["tool_calls"] <= 8
    assert meta["llm_calls"] <= 15
    assert meta["terminal_state"] in ("PROPOSED", "ESCALATED")


async def check_citations(pool: asyncpg.Pool, outcome: Outcome) -> None:
    refs = {
        r["id"]
        for r in await pool.fetch(
            "select id from ocp.evidence where incident_id = $1", outcome.incident_id
        )
    }
    hypotheses = await pool.fetch(
        "select evidence_ids from ocp.hypotheses where incident_id = $1 and status = 'active'",
        outcome.incident_id,
    )
    for h in hypotheses:
        assert h["evidence_ids"] and set(h["evidence_ids"]) <= refs
    for p in await pool.fetch(
        "select evidence_ids from ocp.proposals where incident_id = $1", outcome.incident_id
    ):
        assert set(p["evidence_ids"]) <= refs
    disproofs = await pool.fetchval(
        "select count(*) from ocp.evidence where incident_id = $1 and purpose = 'disproof'",
        outcome.incident_id,
    )
    assert disproofs <= 1


async def check_flags(pool: asyncpg.Pool, outcome: Outcome, scenario: Scenario) -> None:
    flagged = {
        r["ref"]
        for r in await pool.fetch(
            "select ref from ocp.evidence where incident_id = $1 and suspicious_content",
            outcome.incident_id,
        )
    }
    assert flagged == set(scenario.data["expect"].get("suspicious_refs", []))
    for secret in scenario.data["expect"].get("never_stored", []):
        stored = await pool.fetchval(
            "select count(*) from ocp.evidence where incident_id = $1 and payload::text like $2",
            outcome.incident_id,
            f"%{secret}%",
        )
        assert stored == 0, f"{secret!r} reached the evidence store"
