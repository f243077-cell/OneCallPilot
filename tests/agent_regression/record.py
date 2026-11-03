"""Record the tool outputs of the regression scenarios from the live testbed (B2.6, TEST-002).

Run on the integration host with the whole stack up (`ocp up`), from `backend/`:

    uv run python ../tests/agent_regression/record.py 2 5      # scenarios 2 and 5
    uv run python ../tests/agent_regression/record.py all

For each scenario: `chaos reset`, the detector warm-up (240 s), `chaos inject`,
then wait for the incident the real detector opens. Once it is open, record
what the scenario's scripted tool calls would read:

- ``alert``: the incident's real trigger (service, signals, observed_at);
- ``ledger``: the real deploy ledger;
- ``logs``: the newest 50 real log lines per queried service, plus the newest
  50 ERROR/CRITICAL ones (Loki, the same LogQL selector as ``query_logs``);
- ``metrics``: the real series of every queried template, as ``points``
  (Prometheus, the same PromQL as ``query_metrics``);
- ``containers``: the real state of the queried services' containers.

The scripted model, the runner's dry run and the expectation are kept from the
synthetic file. The result goes to ``scenarios/recorded/<same name>.yaml``.
Nothing is written if a value of `.env` or `.env.local` appears in it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import yaml

from oncallpilot.agent.tools.logql import selector
from oncallpilot.agent.tools.promql import render

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCENARIOS = HERE / "scenarios"
RECORDED = SCENARIOS / "recorded"
LOKI = "http://127.0.0.1:3100"
PROMETHEUS = "http://127.0.0.1:9090"
WARMUP_SECONDS = 240
INCIDENT_TIMEOUT = 600
SETTLE_AFTER_INCIDENT = 30
LOG_LINES = 50
API_RETRY_SECONDS = 120
ATTEMPTS = 2
NAMES = {
    1: "memory-leak",
    2: "bad-deploy",
    3: "db-pool",
    4: "cache-outage",
    5: "traffic-spike",
    6: "config-crash",
    7: "slow-dependency",
    8: "log-injection",
}


def env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for name in (".env", ".env.local"):
        path = ROOT / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip("\"'")
    return values


def say(text: str) -> None:
    print(f"[record {datetime.now(UTC):%H:%M:%S}] {text}", flush=True)


def chaos(*words: str) -> None:
    subprocess.run(["uv", "run", "--project", "chaos-shop/cli", "chaos", *words],
                   cwd=ROOT, check=True)  # fmt: skip


class Api:
    def __init__(self, http: httpx.Client, env: dict[str, str]) -> None:
        self.http, self.env = http, env
        self.api = env.get("OCP_API_URL", "http://127.0.0.1:8000").rstrip("/")
        self.sign_in()

    def sign_in(self) -> None:
        """A fresh Supabase token (they last an hour; a full recording takes longer)."""
        base = self.env["SUPABASE_URL"].rstrip("/")
        reply = self.http.post(f"{base}/auth/v1/token", params={"grant_type": "password"},
                               headers={"apikey": self.env["SUPABASE_ANON_KEY"]},
                               json={"email": self.env["OCP_TEST_EMAIL"],
                                     "password": self.env["OCP_TEST_PASSWORD"]})  # fmt: skip
        reply.raise_for_status()
        self.headers = {"Authorization": f"Bearer {reply.json()['access_token']}"}

    def get(self, path: str) -> Any:
        """Retried for 2 minutes: the API answers 500 while the laptop's internet
        (and with it Supabase) is briefly gone."""
        deadline = time.monotonic() + API_RETRY_SECONDS
        while True:
            try:
                reply = self.http.get(f"{self.api}{path}", headers=self.headers)
                if reply.status_code == 401:
                    self.sign_in()
                    reply = self.http.get(f"{self.api}{path}", headers=self.headers)
                if reply.status_code < 500:
                    reply.raise_for_status()
                    return reply.json()
                problem = f"HTTP {reply.status_code}"
            except httpx.TransportError as exc:
                problem = type(exc).__name__
            if time.monotonic() > deadline:
                raise RuntimeError(f"GET {path}: {problem} for {API_RETRY_SECONDS} s")
            say(f"GET {path}: {problem}; retrying")
            time.sleep(10)

    def resolve_open(self) -> None:
        for item in self.get("/incidents?limit=50")["items"]:
            if item["status"] != "resolved":
                self.http.post(f"{self.api}/incidents/{item['id']}/resolve",
                               json={"reason": "recording_cleanup"},
                               headers=self.headers)  # fmt: skip


def calls(model: dict[str, Any]) -> list[dict[str, Any]]:
    found = [c for turn in model.get("gather", []) for c in turn]
    disproof = model.get("disproof")
    if disproof:
        found.append({"tool": disproof["tool"], "args": disproof["params"]})
    return found


def ledger() -> list[dict[str, Any]]:
    out = subprocess.run(
        ["docker", "run", "--rm", "--log-driver", "json-file",
         "-v", "oncallpilot_deploy_ledger:/l:ro", "redis:7.4.11-alpine",
         "cat", "/l/deploys.jsonl"],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def logs(http: httpx.Client, service: Any, end: datetime) -> list[dict[str, Any]]:
    """The newest 50 lines, plus the newest 50 ERROR/CRITICAL ones (the replay
    filters by level, so the errors must be there even when INFO lines are many)."""
    found: dict[tuple[int, str], dict[str, Any]] = {}
    for query in (selector(service, "ANY", None), selector(service, "ERROR", None)):
        reply = http.get(f"{LOKI}/loki/api/v1/query_range", params={
            "query": query, "limit": LOG_LINES, "direction": "backward",
            "start": int((end - timedelta(minutes=15)).timestamp() * 1e9),
            "end": int(end.timestamp() * 1e9),
        })  # fmt: skip
        reply.raise_for_status()
        for stream in reply.json()["data"]["result"]:
            for at, text in stream["values"]:
                try:
                    line = json.loads(text)
                except ValueError:
                    continue
                if isinstance(line, dict) and "level" in line:
                    found[(int(at), text)] = line
    return [found[key] for key in sorted(found, reverse=True)]


def series(http: httpx.Client, promql: str, observed: datetime) -> list[list[Any]]:
    reply = http.get(f"{PROMETHEUS}/api/v1/query_range", params={
        "query": promql, "step": "15s",
        "start": (observed - timedelta(minutes=35)).timestamp(),
        "end": (observed + timedelta(minutes=5)).timestamp(),
    })  # fmt: skip
    reply.raise_for_status()
    result = reply.json()["data"]["result"]
    if not result:
        return []
    return [[float(at), round(float(value), 6)] for at, value in result[0]["values"]
            if value not in ("NaN", "+Inf", "-Inf")]  # fmt: skip


def containers(service: str) -> list[dict[str, Any]]:
    ids = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=oncallpilot.service={service}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    if not ids:
        return []
    found = json.loads(subprocess.run(["docker", "inspect", *ids], capture_output=True,
                                      text=True, check=True).stdout)  # fmt: skip
    out = []
    for c in found:
        if c["State"]["Status"] == "created":
            continue  # a stopped slot that never ran
        out.append({
            "name": c["Name"].lstrip("/"),
            "service": service,
            "release": c["Config"]["Labels"].get("oncallpilot.release", ""),
            "restarts": c["RestartCount"],
            "oom_killed": c["State"]["OOMKilled"],
            "state": c["State"]["Status"],
            "exit_code": c["State"]["ExitCode"],
            "health": (c["State"].get("Health") or {}).get("Status", ""),
        })  # fmt: skip
    return out


def record(number: int, api: Api, http: httpx.Client, secrets: list[str]) -> Path:
    name = NAMES[number]
    source = next(SCENARIOS.glob(f"s{number}_*.yaml"))
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert data["name"] == name, (data["name"], name)

    api.resolve_open()
    say(f"{number} {name}: chaos reset, then {WARMUP_SECONDS} s warm-up")
    chaos("reset")
    time.sleep(WARMUP_SECONDS)
    known = {i["id"] for i in api.get("/incidents?limit=50")["items"]}
    say(f"{number} {name}: inject")
    chaos("inject", name)
    deadline = time.monotonic() + INCIDENT_TIMEOUT
    incident = None
    while incident is None:
        fresh = [i for i in api.get("/incidents?limit=50")["items"] if i["id"] not in known]
        incident = fresh[-1] if fresh else None  # the first one opened
        if incident is None:
            if time.monotonic() > deadline:
                raise RuntimeError(f"no incident within {INCIDENT_TIMEOUT} s")
            time.sleep(5)
    say(f"{number} {name}: incident {incident['service']} {incident['title']!r}")
    time.sleep(SETTLE_AFTER_INCIDENT)
    detail = api.get(f"/incidents/{incident['id']}")
    trigger = detail["trigger"]
    observed = datetime.fromisoformat(trigger["observed_at"].replace("Z", "+00:00"))

    services_logged = sorted({c["args"]["service"] for c in calls(data["model"])
                              if c["tool"] == "query_logs"})  # fmt: skip
    templates = sorted({(c["args"]["template"], c["args"]["service"])
                        for c in calls(data["model"]) if c["tool"] == "query_metrics"})  # fmt: skip
    services_health = sorted({c["args"]["service"] for c in calls(data["model"])
                              if c["tool"] == "get_service_health"})  # fmt: skip
    now = datetime.now(UTC)
    recorded: dict[str, Any] = {
        "name": name,
        "now_offset_s": int((now - observed).total_seconds()),
        "alert": {
            "service": detail["service"],
            "observed_at": trigger["observed_at"],
            "signals": trigger["signals"],
        },  # fmt: skip
        "ledger": ledger(),
        "logs": [{"match": f'service="{s}"', "lines": logs(http, s, now)} for s in services_logged],
        "metrics": [
            {"match": render(t, s), "points": series(http, render(t, s), observed)}
            for t, s in templates
        ],  # fmt: skip
        "containers": [c for s in services_health or [detail["service"]] for c in containers(s)],
        "probes_ok": data.get("probes_ok", True),
        "runner": data["runner"],
        "model": data["model"],
        "expect": data["expect"],
    }
    if "seed_logs" in data:
        recorded["seed_logs"] = data["seed_logs"]
    text = (
        f"# Scenario {number}, {name}: RECORDED from the live testbed on {now:%Y-%m-%d %H:%M} UTC\n"
        f"# by tests/agent_regression/record.py (B2.6). Model, runner and expect are copied\n"
        f"# from scenarios/{source.name}; the tool outputs are real.\n"
        + yaml.safe_dump(recorded, sort_keys=False, allow_unicode=True, width=100)
    )
    leaked = [s for s in secrets if s in text]
    if leaked:
        raise RuntimeError(
            f"{len(leaked)} secret value(s) from .env would be committed; not written"
        )
    RECORDED.mkdir(exist_ok=True)
    target = RECORDED / source.name
    target.write_bytes(text.encode("utf-8"))  # LF on Windows too
    say(f"{number} {name}: wrote {target.relative_to(ROOT)}")
    return target


def main(argv: list[str]) -> int:
    numbers = list(NAMES) if argv == ["all"] else [int(a) for a in argv]
    env = env_values()
    secrets = [v for k, v in env.items() if len(v) >= 12 and any(
        word in k for word in ("KEY", "TOKEN", "PASSWORD", "SECRET", "URL"))
        and v not in ("http://127.0.0.1:8000",)]  # fmt: skip
    failed = []
    with httpx.Client(timeout=20) as http:
        api = Api(http, env)
        for number in numbers:
            for attempt in range(1, ATTEMPTS + 1):
                try:
                    record(number, api, http, secrets)
                    break
                except Exception as exc:  # noqa: BLE001 - one scenario failing must not stop the rest
                    say(f"{number} {NAMES[number]}: attempt {attempt} FAILED "
                        f"{type(exc).__name__}: {exc}")  # fmt: skip
            else:
                failed.append(number)
        chaos("reset")
    say(f"done; failed: {failed or 'none'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
