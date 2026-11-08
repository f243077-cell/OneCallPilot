"""Record the real model's answers to the recorded tool outputs (B2.6, TEST-002).

For each scenario in ``scenarios/recorded/``, the real agent pipeline runs as in
``replay.py`` (recorded Loki, Prometheus and Docker answers, Postgres, Redis, the
signed dry run), but with the real LLM. Every gather turn and structured reply
is captured and written to ``scenarios/recorded_model/<scenario>.<prompt
version>.yaml``, which ``test_scenarios.py`` then replays with FakeProvider: no
network in CI (``CLAUDE.md`` §7).

A recording is written only when the real model reaches the scenario's expected
outcome (``expect``); a miss fails here, so the replay suite only ever pins
behaviour that is right (with ``OCP_RECORD_MISSES=<folder>`` the miss is written
there for inspection). Where the expected outcome is an escalation (scenarios 7
and 8), any escalation counts, as in Gate G2; the recording pins the reason and
the evidence flagged as suspicious that the real model's run produced.

Never collected by CI (the file name does not start with ``test_``); run on
purpose, with the stack-independent test containers of ``tests/integration``:

    OCP_RECORD_MODEL=1 OPENROUTER_API_KEY=… LLM_MODEL=google/gemini-3.8-flash \\
    PROMPT_VERSION=v2 uv run pytest -c pyproject.toml ../tests/agent_regression/record_model.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
import yaml
from pydantic import SecretStr
from redis.asyncio import Redis
from replay import INVALID, RECORDED_MODEL, SCENARIOS, Scenario, load, run

from oncallpilot.agent.providers.base import (
    InvalidStructuredOutputError,
    LLMProvider,
    LLMTurn,
    M,
    Msg,
    Structured,
    ToolSpec,
)
from oncallpilot.agent.providers.openrouter import OpenRouterProvider

SOURCES = sorted((SCENARIOS / "recorded").glob("*.yaml"))
KEYS = {
    "HypothesisSetOut": "hypotheses",
    "DisproofPlanOut": "disproof",
    "ReflectionOut": "reflection",
    "ProposalOut": "proposal",
}


class RecordingProvider:
    """Passes every call to the real provider and keeps what it answered."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.gather: list[list[dict[str, Any]]] = []
        self.replies: dict[str, list[Any]] = {}

    async def step_with_tools(self, messages: list[Msg], tools: list[ToolSpec]) -> LLMTurn:
        turn = await self.inner.step_with_tools(messages, tools)
        # An empty list is a turn without tool calls: gathering ends there.
        self.gather.append([{"tool": c.name, "args": c.arguments} for c in turn.tool_calls])
        return turn

    async def structured(self, messages: list[Msg], schema: type[M]) -> Structured[M]:
        replies = self.replies.setdefault(KEYS[schema.__name__], [])
        try:
            reply = await self.inner.structured(messages, schema)
        except InvalidStructuredOutputError:
            replies.append(INVALID)
            raise
        replies.append(reply.value.model_dump(mode="json"))
        return reply

    def script(self) -> dict[str, Any]:
        model: dict[str, Any] = {"gather": self.gather}
        for key, replies in self.replies.items():
            model[key] = replies[0] if len(replies) == 1 else replies
        return model


async def outcome_of(pool: asyncpg.Pool, incident_id: Any) -> dict[str, Any]:
    row = await pool.fetchrow(
        "select status::text as status, status_reason from ocp.incidents where id = $1",
        incident_id,
    )
    assert row is not None
    if row["status"] == "awaiting_approval":
        proposal = await pool.fetchrow(
            "select action, params from ocp.proposals where incident_id = $1", incident_id
        )
        assert proposal is not None
        return {"phase": "PROPOSED", "action": proposal["action"], "params": proposal["params"]}
    return {"phase": "ESCALATED", "escalation_reason": row["status_reason"]}


def matches(expect: dict[str, Any], got: dict[str, Any]) -> bool:
    keys = ("phase", "action", "params") if expect["phase"] == "PROPOSED" else ("phase",)
    return all(expect.get(k) == got.get(k) for k in keys)


async def flagged(pool: asyncpg.Pool, incident_id: Any) -> list[str]:
    rows = await pool.fetch(
        "select ref from ocp.evidence where incident_id = $1 and suspicious_content", incident_id
    )
    return sorted(str(r["ref"]) for r in rows)


@pytest.mark.timeout(600)
@pytest.mark.parametrize("path", SOURCES, ids=[p.stem for p in SOURCES])
async def test_record_model(
    path: Path, pool: asyncpg.Pool, redis_client: Redis, tmp_path: Path
) -> None:
    if os.environ.get("OCP_RECORD_MODEL") != "1":
        pytest.skip("set OCP_RECORD_MODEL=1 to call the real model on purpose")
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        pytest.skip("OPENROUTER_API_KEY is not set")
    model = os.environ.get("LLM_MODEL", "google/gemini-3.8-flash")
    version = os.environ.get("PROMPT_VERSION", "v1")

    source = load(path)
    data = dict(source.data)
    data["prompt_version"] = version
    scenario = Scenario(path, data)
    async with httpx.AsyncClient() as http:
        recorder = RecordingProvider(OpenRouterProvider(http, SecretStr(key), model))
        outcome = await run(scenario, pool, redis_client, tmp_path, provider=recorder)
    got = await outcome_of(pool, outcome.incident_id)
    expect = dict(source.data["expect"])
    data["model"] = recorder.script()
    if not matches(expect, got):
        misses = os.environ.get("OCP_RECORD_MISSES")
        if misses:
            data["expect"] = got
            Path(misses).mkdir(parents=True, exist_ok=True)
            miss = Path(misses) / f"{path.stem}.{version}.miss.yaml"
            miss.write_text(yaml.safe_dump(data, sort_keys=False, width=100), encoding="utf-8")
        pytest.fail(f"{path.stem}: the real model reached {got}, expected {expect}")
    if expect["phase"] == "ESCALATED":
        expect["escalation_reason"] = got["escalation_reason"]
        if "suspicious_refs" in expect:
            expect["suspicious_refs"] = await flagged(pool, outcome.incident_id)
            assert expect["suspicious_refs"], "the planted text was not flagged suspicious"
    data["expect"] = expect
    target = RECORDED_MODEL / f"{path.stem}.{version}.yaml"
    header = (
        f"# {source.name}: the real model's answers to the tool outputs of\n"
        f"# scenarios/recorded/{path.name}, recorded {datetime.now(UTC):%Y-%m-%d %H:%M} UTC\n"
        f"# by tests/agent_regression/record_model.py (B2.6): {model}, prompts {version}.\n"
        "# Replayed with FakeProvider by test_scenarios.py; regenerate only on purpose.\n"
    )
    RECORDED_MODEL.mkdir(exist_ok=True)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    target.write_text(header + body, encoding="utf-8")
