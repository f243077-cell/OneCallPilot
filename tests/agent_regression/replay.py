"""Replay one scenario through the real agent pipeline (TEST-002).

A scenario file (``scenarios/*.yaml``) holds:

- ``alert``: the detector alert that opens the incident;
- ``ledger``: deploy-ledger records (the rest of each record is filled in);
- ``logs``, ``metrics``, ``containers``: what Loki, Prometheus and
  socket-proxy-ro answer. Today these are synthetic; task B2.6 replaces them
  with outputs recorded from the live testbed;
- ``model``: the scripted FakeProvider (gather turns and structured replies);
- ``runner``: what the fake runner's dry run reports;
- ``expect``: the terminal state and the proposal or escalation reason.

Everything else is production code: ``DbEffects`` on Postgres and Redis,
the five read-only tools over mocked HTTP, redaction, citations, reflection
rules, prompts v1, catalogue validation and the signed dry run.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import asyncpg
import httpx
import yaml
from fake_runner import fake_runner
from oncallpilot_contracts.api import AlertIn
from oncallpilot_contracts.ledger import DeployRecord
from oncallpilot_contracts.runner import RunnerRequest
from redis.asyncio import Redis

from oncallpilot.agent.effects import DbEffects
from oncallpilot.agent.prompts import FilePrompts
from oncallpilot.agent.proposer import RunnerProposer
from oncallpilot.agent.providers.base import LLMTurn, ToolCall, Usage
from oncallpilot.agent.providers.embeddings import FakeEmbeddingProvider
from oncallpilot.agent.providers.fake import FakeProvider
from oncallpilot.agent.run import Investigation
from oncallpilot.agent.state_machine import AgentState
from oncallpilot.agent.toolbox import ToolBox
from oncallpilot.agent.tools.clients import LokiClient, PrometheusClient
from oncallpilot.db.ingest import ingest_alert
from oncallpilot.events import EventStream
from oncallpilot.runner_link import RunnerLink
from tests.runner_replies import LINK_KEY, dry_run_reply

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = Path(__file__).parent / "scenarios"
LEDGER_TEMPLATE = json.loads(
    (ROOT / "contracts" / "fixtures" / "ledger" / "deploys.jsonl").read_text().splitlines()[0]
)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Scenario:
    path: Path
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.data["name"])

    @property
    def observed_at(self) -> datetime:
        return parse_time(self.data["alert"]["observed_at"])

    @property
    def now(self) -> datetime:
        return self.observed_at + timedelta(seconds=int(self.data.get("now_offset_s", 180)))


def load(path: Path) -> Scenario:
    return Scenario(path, yaml.safe_load(path.read_text(encoding="utf-8")))


# --- mocked backends ------------------------------------------------------------------


def _window(params: dict[str, list[str]]) -> tuple[float, float]:
    return int(params["start"][0]) / 1e9, int(params["end"][0]) / 1e9


@dataclass
class Backends:
    scenario: Scenario
    loki_queries: list[str] = field(default_factory=list)
    prom_queries: list[str] = field(default_factory=list)

    def _logs_for(self, logql: str) -> list[dict[str, Any]]:
        for entry in self.scenario.data.get("logs", []):
            if entry["match"] in logql:
                lines = list(entry["lines"])
                break
        else:
            return []
        if 'level=~"ERROR|CRITICAL"' in logql:
            lines = [ln for ln in lines if ln["level"] in ("ERROR", "CRITICAL")]
        if "|= " in logql:
            needle = json.loads(logql.split("|= ", 1)[1].split(" ", 1)[0])
            lines = [ln for ln in lines if needle in json.dumps(ln)]
        return lines

    def loki(self, request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        logql = params["query"][0]
        self.loki_queries.append(logql)
        if request.url.path.endswith("/query"):  # exception counts by exc_type
            counts: dict[str, int] = {}
            for line in self._logs_for(logql):
                if line.get("exc_type"):
                    counts[line["exc_type"]] = counts.get(line["exc_type"], 0) + 1
            result = [{"metric": {"exc_type": t}, "value": [0, str(n)]} for t, n in counts.items()]
            return httpx.Response(200, json={"status": "success", "data": {"result": result}})
        start, end = _window(params)
        lines = self._logs_for(logql)
        values = []
        for i, line in enumerate(lines):
            at = end - (i + 1) * max(1.0, (end - start) / (len(lines) + 1))
            values.append([str(int(at * 1e9)), json.dumps(line)])
        streams = [
            {"stream": {"level": ln["level"]}, "values": [v]}
            for ln, v in zip(lines, values, strict=True)
        ]
        return httpx.Response(200, json={"status": "success", "data": {"result": streams}})

    def prometheus(self, request: httpx.Request) -> httpx.Response:
        params = parse_qs(urlparse(str(request.url)).query)
        promql = params["query"][0]
        self.prom_queries.append(promql)
        start, end = float(params["start"][0]), float(params["end"][0])
        step = int(params["step"][0].rstrip("s"))
        for entry in self.scenario.data.get("metrics", []):
            if entry["match"] in promql:
                change = parse_time(entry["change_at"]).timestamp()
                points = []
                t = start
                while t <= end:
                    value = entry["before"] if t < change else entry["after"]
                    points.append([t, str(value)])
                    t += step
                result = [{"metric": {}, "values": points}]
                break
        else:
            result = []
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    def docker(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET", "the agent only reads through socket-proxy-ro"
        containers = self.scenario.data.get("containers", [])
        if request.url.path == "/containers/json":
            filters = json.loads(request.url.params["filters"])
            wanted = [f.split("=", 1)[1] for f in filters["label"] if "service=" in f]
            return httpx.Response(
                200,
                json=[{"Id": c["name"], "Names": [f"/{c['name']}"]}
                      for c in containers if not wanted or c["service"] in wanted],
            )  # fmt: skip
        name = request.url.path.split("/")[2]
        c = next(c for c in containers if c["name"] == name)
        return httpx.Response(
            200,
            json={
                "Name": f"/{name}",
                "RestartCount": c.get("restarts", 0),
                "State": {
                    "Status": c.get("state", "running"),
                    "OOMKilled": c.get("oom_killed", False),
                    "ExitCode": c.get("exit_code", 0),
                    "StartedAt": "2026-10-21T09:00:00Z",
                    "Health": {"Status": c.get("health", "healthy")},
                },
                "Config": {"Labels": {"oncallpilot.service": c["service"],
                                      "oncallpilot.release": c.get("release", "1.4.0")}},
            },
        )  # fmt: skip

    def probes(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if self.scenario.data.get("probes_ok", True) else 503)


# --- the run --------------------------------------------------------------------------


def turns(model: dict[str, Any]) -> list[LLMTurn]:
    out = []
    for i, turn in enumerate(model.get("gather", [])):
        calls = tuple(
            ToolCall(id=f"t{i}-{j}", name=c["tool"], arguments=dict(c.get("args") or {}))
            for j, c in enumerate(turn)
        )
        out.append(LLMTurn(tool_calls=calls, done=False, usage=Usage(400, 40)))
    return out


def structured(model: dict[str, Any]) -> dict[str, list[Any]]:
    keys = {
        "hypotheses": "HypothesisSetOut",
        "disproof": "DisproofPlanOut",
        "reflection": "ReflectionOut",
        "proposal": "ProposalOut",
    }
    return {schema: [model[key]] for key, schema in keys.items() if key in model}


def write_ledger(scenario: Scenario, folder: Path) -> Path:
    path = folder / "deploys.jsonl"
    lines = []
    for record in scenario.data.get("ledger", []):
        derived = {
            "deploy_id": str(uuid.uuid4()),
            "image_tag": f"chaos-shop/{record['service']}:{record['release']}",
        }
        line = json.dumps(LEDGER_TEMPLATE | derived | record)
        DeployRecord.model_validate_json(line)  # a bad fixture fails here, not silently
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@dataclass
class Outcome:
    state: AgentState
    incident_id: uuid.UUID
    runner_requests: list[RunnerRequest]
    backends: Backends


async def run(scenario: Scenario, pool: asyncpg.Pool, redis: Redis, tmp_path: Path) -> Outcome:
    alert = AlertIn.model_validate({"source": "detector", **scenario.data["alert"]})
    async with pool.acquire() as conn:
        opened = await ingest_alert(
            conn, alert, now=scenario.observed_at, detector_config_version="regression",
            benchmark_run_id=None,
        )  # fmt: skip
    backends = Backends(scenario)
    ledger = write_ledger(scenario, tmp_path)
    runner_state = scenario.data.get("runner", {})

    def reply(request: RunnerRequest) -> dict[str, Any]:
        return dry_run_reply(
            str(request.request_id),
            active=runner_state.get("active_release"),
            replicas=runner_state.get("replicas", 1),
        )

    clock = scenario.now
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(backends.loki)) as loki_http,
        httpx.AsyncClient(transport=httpx.MockTransport(backends.prometheus)) as prom_http,
        httpx.AsyncClient(
            transport=httpx.MockTransport(backends.docker), base_url="http://socket-proxy-ro:2375"
        ) as docker,
        httpx.AsyncClient(transport=httpx.MockTransport(backends.probes)) as probes,
        fake_runner(redis, reply) as runner,
    ):
        effects = DbEffects(
            pool=pool,
            events=EventStream(redis),
            tools=ToolBox(
                loki=LokiClient(loki_http, "http://loki:3100"),
                prometheus=PrometheusClient(prom_http, "http://prometheus:9090"),
                docker=docker,
                http=probes,
                ledger=ledger,
                pool=pool,
                embedder=FakeEmbeddingProvider(),
            ),
            incident_id=opened.incident_id,
            provider_name="fake",
            model="fake-regression",
            proposer=RunnerProposer(
                catalogue_path=ROOT / "contracts" / "actions.yaml",
                targets_path=ROOT / "runner" / "targets.yaml",
                ledger_path=ledger,
                link=RunnerLink(redis, LINK_KEY, clock=lambda: clock, wait_seconds=2.0),
            ),
            clock=lambda: clock,
        )
        model = scenario.data["model"]
        state = await Investigation(
            provider=FakeProvider(turns=turns(model), structured=structured(model)),
            effects=effects,
            prompts=FilePrompts("v1"),
        ).run()
        requests = list(runner.requests)
    return Outcome(state, opened.incident_id, requests, backends)
