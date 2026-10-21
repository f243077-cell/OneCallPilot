"""Copilot metrics after a database-backed investigation (B3.5; NFR-006)."""

from __future__ import annotations

from typing import Any

import asyncpg
from test_agent_db import run, script, setup  # noqa: F401  (setup is a fixture)

from oncallpilot.metrics import REGISTRY


def value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_an_investigation_is_measured(setup: Any, pool: asyncpg.Pool) -> None:  # noqa: F811
    incident_id, effects = setup
    before = {
        "logs": value("ocp_agent_tool_calls_total", tool="query_logs", purpose="gather",
                      outcome="stored"),
        "escalated": value("ocp_agent_escalations_total", reason="dry_run_unavailable"),
        "llm": value("ocp_llm_call_seconds_count", kind="hypothesize", provider="fake"),
        "runs": value("ocp_agent_duration_seconds_count", terminal_state="ESCALATED"),
    }  # fmt: skip
    await run(effects, script())
    assert value("ocp_agent_tool_calls_total", tool="query_logs", purpose="gather",
                 outcome="stored") == before["logs"] + 1  # fmt: skip
    assert value("ocp_agent_escalations_total", reason="dry_run_unavailable") == (
        before["escalated"] + 1
    )
    assert value("ocp_llm_call_seconds_count", kind="hypothesize", provider="fake") == (
        before["llm"] + 1
    )
    assert value("ocp_agent_duration_seconds_count", terminal_state="ESCALATED") == (
        before["runs"] + 1
    )
    duration = value(
        "ocp_agent_incident_duration_seconds", incident_id=str(incident_id),
        terminal_state="ESCALATED",
    )  # fmt: skip
    meta = await pool.fetchval("select agent_meta from ocp.incidents where id = $1", incident_id)
    assert duration == meta["duration_ms"] / 1000
