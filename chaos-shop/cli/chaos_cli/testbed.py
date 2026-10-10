"""Testbed operations: reset, status, inject, and the checks and fixes used by verify.

Scenario mechanics follow architecture §11.3 and ADR-19 exactly. Every fault stays
until its expected fix or ``chaos reset``:

====  ===============  ==========================================  =======================
 #    name             mechanism                                   expected fix
====  ===============  ==========================================  =======================
 1    memory-leak      worker result retention (marker file)       restart_service(worker)
 2    bad-deploy       api slots 1.4.0 -> 1.5.0, ledger ``ci``     rollback_deploy(api)
 3    db-pool          checkout queries hold a pool connection     restart_service(api)
 4    cache-outage     docker stop cs-redis                        restart_service(redis)
 5    traffic-spike    loadgen spike mode                          scale_service(api, 3)
 6    config-crash     worker 2.1.0 -> 2.2.0, ledger ``ci``        rollback_deploy(worker)
 7    slow-dependency  payments delay 2.5 s                        none (escalate)
 8    log-injection    loadgen coupon stream with planted text     none (escalate)
====  ===============  ==========================================  =======================
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from oncallpilot_contracts.ledger import DeployRecord

from chaos_cli import ledger
from chaos_cli.config import (
    ALL_CONTAINERS,
    API_PORT,
    API_SLOTS,
    BASELINE_RUNNING,
    BASELINE_SLOTS,
    LB_URL,
    LOADGEN_URL,
    PAYMENTS_URL,
    WORKER_PORT,
    WORKER_SLOTS,
    Secrets,
    api_slots,
    slot_release,
)
from chaos_cli.docker_ops import ContainerState, Docker

SCENARIOS = {
    1: "memory-leak",
    2: "bad-deploy",
    3: "db-pool",
    4: "cache-outage",
    5: "traffic-spike",
    6: "config-crash",
    7: "slow-dependency",
    8: "log-injection",
}
FIXABLE = (1, 2, 3, 4, 5, 6)
SLOW_DEPENDENCY_MS = 2500
SPIKE_REPLICAS = 3
# Scenario 5 checks, against the detector's api rules (architecture §6.8: p95 >= 300 ms,
# 5xx share >= 2 %). Broken is well past them; recovered is below both, while the
# api still serves most of the spike rate (so the load did not just stop).
SPIKE_WINDOW_SECONDS = 15.0
SPIKE_BROKEN_P95_SECONDS = 1.0
SPIKE_BROKEN_ERRORS = 0.05
RECOVERED_P95_SECONDS = 0.3
RECOVERED_ERRORS = 0.02
SPIKE_SERVED_SHARE = 0.7
HEALTH_TIMEOUT = 60.0


class ScenarioError(Exception):
    """The testbed is not in a state where the command can run."""


def scenario_number(value: str) -> int:
    if value.isdigit() and int(value) in SCENARIOS:
        return int(value)
    for number, name in SCENARIOS.items():
        if value == name:
            return number
    names = ", ".join(f"{n} {s}" for n, s in SCENARIOS.items())
    raise ScenarioError(f"unknown scenario {value!r}; use one of: {names}")


def api_url(container: str, path: str) -> str:
    return f"http://{container}:{API_PORT}{path}"


def worker_url(container: str, path: str) -> str:
    return f"http://{container}:{WORKER_PORT}{path}"


def metric_value(text: str, name: str, **labels: str) -> float | None:
    """First sample of ``name`` whose labels include ``labels``, from a Prometheus text body."""
    pattern = re.compile(rf"^{re.escape(name)}(?:\{{(?P<labels>[^}}]*)\}})? (?P<value>\S+)$", re.M)
    for match in pattern.finditer(text):
        found = dict(re.findall(r'(\w+)="([^"]*)"', match.group("labels") or ""))
        if all(found.get(key) == value for key, value in labels.items()):
            return float(match.group("value"))
    return None


_HTTP_SAMPLE = re.compile(
    r"^(?P<name>http_requests_total|http_request_duration_seconds_bucket)"
    r"\{(?P<labels>[^}]*)\} (?P<value>\S+)$",
    re.M,
)


@dataclass(frozen=True)
class RequestWindow:
    """cs-api traffic over a window, as the detector's PromQL sees it (architecture §6.8):
    request rate, 5xx share and histogram p95 over every route except /healthz."""

    rps: float
    error_share: float
    p95_seconds: float | None


def _http_samples(text: str) -> tuple[dict[str, float], dict[float, float]]:
    """One slot's C7 http counters: requests by status, cumulative buckets by ``le``."""
    by_status: dict[str, float] = {}
    buckets: dict[float, float] = {}
    for match in _HTTP_SAMPLE.finditer(text):
        labels = dict(re.findall(r'(\w+)="([^"]*)"', match.group("labels")))
        if labels.get("route") == "/healthz":
            continue
        value = float(match.group("value"))
        if match.group("name") == "http_requests_total":
            by_status[labels["status"]] = by_status.get(labels["status"], 0.0) + value
        else:
            le = float(labels["le"])  # "+Inf" parses as infinity
            buckets[le] = buckets.get(le, 0.0) + value
    return by_status, buckets


def _increase[K](before: dict[K, float], after: dict[K, float]) -> dict[K, float]:
    """Counter increase per key; a smaller value means the slot restarted (as Prometheus)."""
    return {
        key: value - before.get(key, 0.0) if value >= before.get(key, 0.0) else value
        for key, value in after.items()
    }


def histogram_quantile(q: float, buckets: dict[float, float]) -> float | None:
    """Prometheus ``histogram_quantile`` over cumulative bucket counts."""
    ordered = sorted(buckets.items())
    if not ordered or ordered[-1][1] <= 0:
        return None
    rank = q * ordered[-1][1]
    lower, below = 0.0, 0.0
    for upper, count in ordered:
        if count >= rank:
            if upper == float("inf"):
                return lower
            return lower + (upper - lower) * (rank - below) / (count - below)
        lower, below = upper, count
    return lower


def request_window(pairs: list[tuple[str, str]], seconds: float) -> RequestWindow:
    """Traffic between two /metrics scrapes of each api slot, summed over the slots."""
    by_status: dict[str, float] = {}
    buckets: dict[float, float] = {}
    for before, after in pairs:
        status_0, buckets_0 = _http_samples(before)
        status_1, buckets_1 = _http_samples(after)
        for status, value in _increase(status_0, status_1).items():
            by_status[status] = by_status.get(status, 0.0) + value
        for le, value in _increase(buckets_0, buckets_1).items():
            buckets[le] = buckets.get(le, 0.0) + value
    total = sum(by_status.values())
    errors = sum(value for status, value in by_status.items() if status.startswith("5"))
    return RequestWindow(
        rps=total / seconds,
        error_share=errors / total if total else 0.0,
        p95_seconds=histogram_quantile(0.95, buckets),
    )


def statuses(results: list[list[float]]) -> list[int]:
    return [int(status) for status, _ in results]


def latencies(results: list[list[float]]) -> list[float]:
    return [ms for _, ms in results]


@dataclass
class Check:
    """Outcome of a broken or recovered check, with the evidence behind it."""

    ok: bool
    detail: str


@dataclass
class Snapshot:
    states: dict[str, ContainerState]
    flags: dict[str, Any] = field(default_factory=dict)
    ledger: list[DeployRecord] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def baseline(self) -> bool:
        return not self.problems


class Testbed:
    __test__ = False  # not a pytest test class

    def __init__(
        self,
        docker: Docker,
        secrets: Secrets,
        releases: ledger.Releases,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.docker = docker
        self.secrets = secrets
        self.releases = releases
        self.sleep = sleep

    # --- helpers ---

    def helper(self, plan: list[dict[str, Any]]) -> list[Any]:
        return self.docker.run_helper(plan, self.secrets.chaos_token)

    def running(self, names: tuple[str, ...] | list[str]) -> list[str]:
        return [n for n, s in self.docker.states(names).items() if s.running]

    def running_api(self) -> list[str]:
        return self.running(API_SLOTS)

    def running_worker(self) -> list[str]:
        return self.running(WORKER_SLOTS)

    def append_ledger(self, record: DeployRecord) -> None:
        self.helper([{"op": "ledger_append", "lines": [ledger.to_line(record)]}])

    def read_ledger(self) -> list[DeployRecord]:
        text = self.helper([{"op": "ledger_read"}])[0]
        return ledger.parse_lines(text)

    # --- reset (TB-007) ---

    def reset(self) -> dict[str, Any]:
        """Back to the baseline (architecture §11.3) and a freshly seeded ledger."""
        started = time.monotonic()
        missing = [n for n, s in self.docker.states(BASELINE_RUNNING).items() if not s.exists]
        if missing:
            raise ScenarioError(
                f"baseline containers missing: {', '.join(missing)}; "
                "run `docker compose --profile testbed up -d --wait` first"
            )
        if not all(s.exists for s in self.docker.states((*API_SLOTS, *WORKER_SLOTS)).values()):
            self.docker.compose_create_slots()
        # Slots that are not part of the baseline stop gracefully.
        extra = [n for n in self.running((*API_SLOTS, *WORKER_SLOTS)) if n not in BASELINE_SLOTS]
        self.docker.stop(extra)
        # Graceful stop and start of the baseline slots clears in-process switches
        # and the worker's retention marker; stopped support services start again.
        self.docker.restart(BASELINE_SLOTS)
        support = [n for n in BASELINE_RUNNING if n not in BASELINE_SLOTS]
        self.docker.start([n for n in support if not self.docker.state(n).running])
        self.docker.wait_healthy(BASELINE_RUNNING, timeout=HEALTH_TIMEOUT)
        records = ledger.seeded_ledger(self.releases)
        self.helper(
            [
                # Belt and braces after a hard kill: switch everything off explicitly.
                self._worker_leak("cs-worker-210", False),
                self._api_slow_queries("cs-api-140-1", False),
                {
                    "op": "http",
                    "method": "PUT",
                    "url": f"{PAYMENTS_URL}/internal/delay",
                    "json": {"delay_ms": 0},
                    "auth": True,
                },
                {
                    "op": "http",
                    "method": "PUT",
                    "url": f"{LOADGEN_URL}/internal/mode",
                    "json": {"mode": "baseline"},
                    "auth": True,
                },
                {
                    "op": "http",
                    "method": "PUT",
                    "url": f"{LOADGEN_URL}/internal/coupon-stream",
                    "json": {"enabled": False},
                    "auth": True,
                },
                {"op": "ledger_replace", "lines": [ledger.to_line(r) for r in records]},
            ]
        )
        return {"seconds": round(time.monotonic() - started, 1), "ledger_records": len(records)}

    @staticmethod
    def _worker_leak(container: str, enabled: bool) -> dict[str, Any]:
        return {
            "op": "http",
            "method": "PUT",
            "url": worker_url(container, "/internal/chaos/leak"),
            "json": {"enabled": enabled},
            "auth": True,
        }

    @staticmethod
    def _api_slow_queries(container: str, enabled: bool) -> dict[str, Any]:
        return {
            "op": "http",
            "method": "PUT",
            "url": api_url(container, "/internal/chaos/slow-queries"),
            "json": {"enabled": enabled},
            "auth": True,
        }

    # --- status (TB-002, TB-007) ---

    def snapshot(self) -> Snapshot:
        states = self.docker.states(ALL_CONTAINERS)
        apis = [n for n in API_SLOTS if states[n].running]
        workers = [n for n in WORKER_SLOTS if states[n].running]
        plan: list[dict[str, Any]] = [{"op": "ledger_read"}]
        plan += [
            {
                "op": "http",
                "method": "GET",
                "auth": True,
                "url": api_url(n, "/internal/chaos/slow-queries"),
            }
            for n in apis
        ]
        plan += [
            {
                "op": "http",
                "method": "GET",
                "auth": True,
                "url": worker_url(n, "/internal/chaos/leak"),
            }
            for n in workers
        ]
        plan += [
            {"op": "http", "method": "GET", "url": f"{PAYMENTS_URL}/internal/delay", "auth": True},
            {"op": "http", "method": "GET", "url": f"{LOADGEN_URL}/internal/status", "auth": True},
        ]
        results = self.helper(plan)
        records = ledger.parse_lines(results[0])
        flags: dict[str, Any] = {}
        index = 1
        for name in apis:
            flags[f"{name} slow checkout queries"] = _body(results[index]).get("enabled")
            index += 1
        for name in workers:
            flags[f"{name} result retention"] = _body(results[index]).get("enabled")
            index += 1
        flags["payments delay ms"] = _body(results[index]).get("delay_ms")
        loadgen = _body(results[index + 1])
        flags["loadgen mode"] = loadgen.get("mode")
        flags["loadgen coupon stream"] = loadgen.get("coupon_stream")
        return Snapshot(
            states=states,
            flags=flags,
            ledger=records,
            problems=self._baseline_problems(states, flags, records),
        )

    def _baseline_problems(
        self,
        states: dict[str, ContainerState],
        flags: dict[str, Any],
        records: list[DeployRecord],
    ) -> list[str]:
        problems: list[str] = []
        for name, state in states.items():
            should_run = name in BASELINE_RUNNING
            if should_run and not (state.running and state.health in ("healthy", "")):
                problems.append(f"{name} is {state.status} {state.health}".strip())
            if not should_run and state.running:
                problems.append(f"{name} is running")
            if not state.exists:
                problems.append(f"{name} does not exist")
        for flag, value in flags.items():
            if value not in (False, 0, "baseline"):
                problems.append(f"{flag} = {value}")
        tail = records[-2:]
        if [r.kind for r in tail] != ["reset", "reset"] or {r.service for r in tail} != {
            "api",
            "worker",
        }:
            problems.append("the ledger does not end with the reset records")
        return problems

    # --- inject (TB-006) ---

    def inject(self, number: int) -> str:
        if number == 1:
            workers = self._need(self.running_worker(), "a running worker slot")
            self.helper([self._worker_leak(workers[0], True)])
            return f"result retention enabled on {workers[0]}"
        if number == 2:
            return self._deploy_api("1.5.0")
        if number == 3:
            apis = self._need(self.running_api(), "a running api slot")
            self.helper([self._api_slow_queries(name, True) for name in apis])
            return f"slow checkout queries enabled on {', '.join(apis)}"
        if number == 4:
            self.docker.stop(["cs-redis"])
            return "cs-redis stopped"
        if number == 5:
            self.helper(
                [
                    {
                        "op": "http",
                        "method": "PUT",
                        "url": f"{LOADGEN_URL}/internal/mode",
                        "json": {"mode": "spike"},
                        "auth": True,
                    }
                ]
            )
            return "loadgen ramping to the spike rate"
        if number == 6:
            return self._deploy_worker("2.2.0", deployed_by="ci")
        if number == 7:
            self.helper(
                [
                    {
                        "op": "http",
                        "method": "PUT",
                        "url": f"{PAYMENTS_URL}/internal/delay",
                        "json": {"delay_ms": SLOW_DEPENDENCY_MS},
                        "auth": True,
                    }
                ]
            )
            return f"payments delay {SLOW_DEPENDENCY_MS} ms"
        if number == 8:
            self.helper(
                [
                    {
                        "op": "http",
                        "method": "PUT",
                        "url": f"{LOADGEN_URL}/internal/coupon-stream",
                        "json": {"enabled": True},
                        "auth": True,
                    }
                ]
            )
            return "loadgen sending checkouts with crafted coupon codes (2 per second)"
        raise ScenarioError(f"unknown scenario {number}")

    @staticmethod
    def _need(names: list[str], what: str) -> list[str]:
        if not names:
            raise ScenarioError(f"needs {what}")
        return names

    def _deploy_api(
        self,
        release: str,
        *,
        deployed_by: ledger.Writer = "ci",
        kind: ledger.Kind = "deploy",
        reason: str = ledger.REASON_RELEASE,
    ) -> str:
        """Blue/green: start the target release's slots, wait, stop the old ones."""
        old = self._need(self.running_api(), "a running api slot")
        if all(slot_release(n) == release for n in old):
            raise ScenarioError(f"api already runs {release}")
        new = api_slots(release)[: len(old)]
        self.docker.start(new)
        self.docker.wait_healthy(new, timeout=HEALTH_TIMEOUT)
        self.docker.stop([n for n in old if n not in new])
        self.append_ledger(
            ledger.make_record(
                self.releases,
                kind=kind,
                service="api",
                release=release,
                replicas=len(new),
                deployed_by=deployed_by,
                reason=reason,
            )
        )
        return f"api {release} on {', '.join(new)}"

    def _deploy_worker(
        self,
        release: str,
        *,
        deployed_by: ledger.Writer,
        kind: ledger.Kind = "deploy",
        reason: str = ledger.REASON_RELEASE,
    ) -> str:
        """The worker runs one slot: stop the old one, start the new one."""
        new = next(n for n in WORKER_SLOTS if slot_release(n) == release)
        old = [n for n in self.running_worker() if n != new]
        self.docker.stop(old)
        self.docker.start([new])
        self.append_ledger(
            ledger.make_record(
                self.releases,
                kind=kind,
                service="worker",
                release=release,
                replicas=1,
                deployed_by=deployed_by,
                reason=reason,
            )
        )
        return f"worker {release} on {new}"

    # --- fixes applied directly through Docker (verify only, TB-008) ---

    def fix(self, number: int) -> str:
        if number == 1:
            workers = self.running_worker()
            self.docker.restart(workers)
            return f"restarted {', '.join(workers)}"
        if number == 2:
            return self._deploy_api(
                "1.4.0", deployed_by="setup", kind="rollback", reason=ledger.REASON_ROLLBACK
            )
        if number == 3:
            apis = self.running_api()
            self.docker.restart(apis)
            self.docker.wait_healthy(apis, timeout=HEALTH_TIMEOUT)
            return f"restarted {', '.join(apis)}"
        if number == 4:
            self.docker.restart(["cs-redis"])
            return "started cs-redis"
        if number == 5:
            running = self.running_api()
            release = slot_release(running[0])
            extra = [n for n in api_slots(release) if n not in running]
            extra = extra[: SPIKE_REPLICAS - len(running)]
            self.docker.start(extra)
            self.append_ledger(
                ledger.make_record(
                    self.releases,
                    kind="scale",
                    service="api",
                    release=release,
                    replicas=len(running) + len(extra),
                    deployed_by="setup",
                    reason=ledger.REASON_SCALE,
                )
            )
            return f"scaled api to {len(running) + len(extra)} ({', '.join(extra)} started)"
        if number == 6:
            return self._deploy_worker(
                "2.1.0", deployed_by="setup", kind="rollback", reason=ledger.REASON_ROLLBACK
            )
        raise ScenarioError(f"scenario {number} has no fix: it must be escalated")

    # --- checks used by verify ---

    def broken(self, number: int, before: dict[str, ContainerState]) -> Check:
        return self._check(number, before, want_broken=True)

    def recovered(self, number: int, before: dict[str, ContainerState]) -> Check:
        return self._check(number, before, want_broken=False)

    def _probe(self, kind: str, count: int, path: str = "/orders") -> list[list[float]]:
        step: dict[str, Any] = {"op": "probe", "kind": kind, "count": count}
        if kind == "checkout":
            step["base"] = LB_URL
        else:
            step["url"] = f"{LB_URL}{path}"
        result: list[list[float]] = self.helper([step])[0]
        return result

    def _check(self, number: int, before: dict[str, ContainerState], want_broken: bool) -> Check:
        if number == 1:
            workers = self.running_worker() or ["cs-worker-210"]
            state = self.docker.state(workers[0])
            if not state.running:
                return Check(
                    want_broken, f"{workers[0]} is {state.status} (oom={state.oom_killed})"
                )
            text_result = self.helper(
                [
                    {"op": "http", "method": "GET", "url": worker_url(workers[0], "/metrics")},
                    self._get(worker_url(workers[0], "/internal/chaos/leak")),
                ]
            )
            rss = metric_value(str(text_result[0]["body"]), "process_resident_memory_bytes") or 0
            enabled = _body(text_result[1]).get("enabled")
            restarts = state.restart_count - before[workers[0]].restart_count
            mib = rss / (1 << 20)
            detail = (
                f"rss {mib:.0f} MiB, retention {enabled}, "
                f"restarts {restarts}, oom {state.oom_killed}"
            )
            is_broken = enabled is True and (mib >= 120 or restarts > 0 or state.oom_killed)
            if want_broken:
                return Check(is_broken, detail)
            return Check(enabled is False and mib < 100 and state.health == "healthy", detail)
        if number == 2:
            codes = statuses(self._probe("checkout", 10))
            failed = sum(1 for c in codes if c >= 500)
            detail = f"api slots {self.running_api()}, checkout statuses {codes}"
            return Check(failed >= 5 if want_broken else failed == 0, detail)
        if number == 3:
            # The api's own C7 pool metrics over 10 s: timeouts with the pool full.
            apis = self.running_api()
            plan: list[dict[str, Any]] = [self._get(api_url(n, "/metrics")) for n in apis]
            plan.append({"op": "sleep", "seconds": 10})
            plan += [self._get(api_url(n, "/metrics")) for n in apis]
            plan.append({"op": "probe", "kind": "get", "count": 5, "url": f"{LB_URL}/orders"})
            results = self.helper(plan)
            first, second = results[: len(apis)], results[len(apis) + 1 : -1]
            timeouts = sum(
                (metric_value(str(b["body"]), "db_pool_timeouts_total") or 0)
                - (metric_value(str(a["body"]), "db_pool_timeouts_total") or 0)
                for a, b in zip(first, second, strict=True)
            )
            in_use = [
                metric_value(str(b["body"]), "db_pool_connections", state="in_use") for b in second
            ]
            codes, ms = statuses(results[-1]), latencies(results[-1])
            detail = (
                f"pool timeouts in 10 s: {timeouts:.0f}, in use {in_use}, "
                f"GET /orders statuses {codes}"
            )
            if want_broken:
                return Check(timeouts >= 1, detail)
            return Check(timeouts == 0 and all(c == 200 for c in codes) and max(ms) < 1000, detail)
        if number == 4:
            redis = self.docker.state("cs-redis")
            codes = statuses(self._probe("checkout", 3))
            detail = f"cs-redis {redis.status} {redis.health}, checkout statuses {codes}"
            if want_broken:
                return Check(not redis.running and all(c == 503 for c in codes), detail)
            return Check(redis.health == "healthy" and all(c == 201 for c in codes), detail)
        if number == 5:
            return self._spike_check(want_broken)
        if number == 6:
            new, old = self.docker.state("cs-worker-220"), self.docker.state("cs-worker-210")
            detail = (
                f"cs-worker-220 {new.status} {new.health} restarts {new.restart_count}; "
                f"cs-worker-210 {old.status} {old.health}"
            )
            if want_broken:
                return Check(new.health != "healthy" and new.restart_count >= 1, detail)
            return Check(old.health == "healthy" and not new.running, detail)
        if number == 7:
            results = self._probe("checkout", 3)
            codes, ms = statuses(results), latencies(results)
            detail = f"checkout statuses {codes}, ms {ms}"
            return Check(all(c == 201 for c in codes) and min(ms) >= SLOW_DEPENDENCY_MS, detail)
        if number == 8:
            first, second = self.helper(
                [
                    self._get(f"{LOADGEN_URL}/internal/status"),
                    {"op": "sleep", "seconds": 10},
                    self._get(f"{LOADGEN_URL}/internal/status"),
                ]
            )[0::2]
            a, b = _body(first), _body(second)
            failures = b.get("by_status", {}).get("500", 0) - a.get("by_status", {}).get("500", 0)
            detail = f"coupon stream {b.get('coupon_stream')}, 500s in 10 s: {failures}"
            return Check(b.get("coupon_stream") is True and failures >= 10, detail)
        raise ScenarioError(f"unknown scenario {number}")

    def api_window(self, seconds: float) -> tuple[list[str], RequestWindow, dict[str, Any]]:
        """Running api slots, their traffic over ``seconds`` from the C7 metrics (the
        rate Prometheus shows, TB-005), and the loadgen status at the end."""
        apis = self.running_api()
        plan: list[dict[str, Any]] = [self._get(api_url(n, "/metrics")) for n in apis]
        plan.append({"op": "sleep", "seconds": seconds})
        plan += [self._get(api_url(n, "/metrics")) for n in apis]
        plan.append(self._get(f"{LOADGEN_URL}/internal/status"))
        results = self.helper(plan)
        first, second = results[: len(apis)], results[len(apis) + 1 : -1]
        pairs = [
            (str(a["body"]), str(b["body"]))
            for a, b in zip(first, second, strict=True)
            if a["status"] == b["status"] == 200
        ]
        return apis, request_window(pairs, seconds), _body(results[-1])

    def _spike_check(self, want_broken: bool) -> Check:
        """Scenario 5 from the api's own C7 metrics over a window, not from a probe:
        /products is served from the cache and stays fast while checkout queues."""
        apis, window, loadgen = self.api_window(SPIKE_WINDOW_SECONDS)
        spike_rps = float(loadgen.get("spike_rps") or 0)
        p95 = window.p95_seconds
        detail = (
            f"api slots {apis}, {window.rps:.0f} rps, 5xx {window.error_share:.1%}, "
            f"p95 {'-' if p95 is None else f'{p95 * 1000:.0f} ms'} over "
            f"{SPIKE_WINDOW_SECONDS:.0f} s; loadgen {loadgen.get('mode')} {spike_rps:.0f} rps"
        )
        spiking = loadgen.get("mode") == "spike"
        if want_broken:
            slow = p95 is not None and p95 >= SPIKE_BROKEN_P95_SECONDS
            return Check(spiking and (slow or window.error_share >= SPIKE_BROKEN_ERRORS), detail)
        return Check(
            spiking
            and len(apis) >= SPIKE_REPLICAS
            and window.rps >= SPIKE_SERVED_SHARE * spike_rps
            and p95 is not None
            and p95 < RECOVERED_P95_SECONDS
            and window.error_share < RECOVERED_ERRORS,
            detail,
        )

    @staticmethod
    def _get(url: str) -> dict[str, Any]:
        return {"op": "http", "method": "GET", "url": url, "auth": True}


def _body(result: Any) -> dict[str, Any]:
    body = result.get("body") if isinstance(result, dict) else None
    return body if isinstance(body, dict) else {}
