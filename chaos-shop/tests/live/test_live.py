"""Live checks against the running testbed (TB-001..TB-005, TB-011).

Run from chaos-shop/ on the host, with the testbed up and running for at least 2 minutes:

    docker compose --profile testbed up -d --wait      # from the repository root
    docker compose --profile testbed-slots create      # the stopped slots (TB-002)
    uv run pytest -m live -v

Only cs-lb is published (127.0.0.1:8080), so /metrics is fetched by a throwaway
python:3.12.15-slim container on the testbed network. The scaling test starts
and stops cs-api-140-2 and cs-api-140-3 with the host's Docker and leaves them
stopped again.
"""

import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

import pytest
from prometheus_client.parser import text_string_to_metric_families

from tests.contract import API_METRICS, WORKER_METRICS, log_problems, metric_problems

pytestmark = pytest.mark.live

NETWORK = "oncallpilot_chaos_net"
FETCH_IMAGE = "python:3.12.15-slim"
FETCH = (
    "import sys, urllib.request; "
    "sys.stdout.write(urllib.request.urlopen(sys.argv[1], timeout=5).read().decode())"
)
MANAGED = {
    "cs-postgres": ("postgres", "17.11"),
    "cs-lb": ("lb", "1.30.5"),
    "cs-redis": ("redis", "7.4.11"),
    "cs-payments": ("payments", "1.0.0"),
    "cs-api-140-1": ("api", "1.4.0"),
    "cs-worker-210": ("worker", "2.1.0"),
}
JSON_LOGGERS = {
    "cs-api-140-1": "api",
    "cs-worker-210": "worker",
    "cs-payments": "payments",
    "cs-lb": "lb",
}
STOPPED_SLOTS = {
    **{f"cs-api-140-{n}": ("api", "1.4.0", str(n)) for n in range(2, 6)},
    **{f"cs-api-150-{n}": ("api", "1.5.0", str(n)) for n in range(1, 6)},
    "cs-worker-220": ("worker", "2.2.0", "1"),
}
LB_URL = "http://127.0.0.1:8080"
TB_011_SECONDS = 10.0
PUBLISHED = {"cs-lb": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}]}}


def docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def fetch(url: str) -> str:
    return docker("run", "--rm", "--network", NETWORK, FETCH_IMAGE, "python", "-c", FETCH, url)


def request_total(text: str, *, include_healthz: bool) -> float:
    total = 0.0
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name != "http_requests_total":
                continue
            if include_healthz or sample.labels["route"] != "/healthz":
                total += sample.value
    return total


def test_every_service_is_running_and_healthy() -> None:
    for name in [*MANAGED, "cs-loadgen"]:
        state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
        assert state["Running"], name
        assert state["Health"]["Status"] == "healthy", name


def test_labels_and_no_published_ports() -> None:
    for name, (service, release) in MANAGED.items():
        labels = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", name))
        ours = {k: v for k, v in labels.items() if k.startswith("oncallpilot.")}
        assert ours == {
            "oncallpilot.managed": "true",
            "oncallpilot.service": service,
            "oncallpilot.release": release,
            "oncallpilot.replica": "1",
            "oncallpilot.logs": "true",
        }, name
    labels = json.loads(docker("inspect", "--format", "{{json .Config.Labels}}", "cs-loadgen"))
    assert not any(k.startswith("oncallpilot.") for k in labels)
    for name in [*MANAGED, *STOPPED_SLOTS, "cs-loadgen"]:
        ports = json.loads(docker("inspect", "--format", "{{json .HostConfig.PortBindings}}", name))
        assert (ports or {}) == PUBLISHED.get(name, {}), f"{name} publishes {ports}"


def test_stopped_slots_exist_with_labels_and_aliases() -> None:
    """TB-002: every slot is pre-created, labelled, aliased, and stopped at baseline."""
    for name, (service, release, replica) in STOPPED_SLOTS.items():
        info = json.loads(docker("inspect", name))[0]
        assert info["State"]["Status"] in ("created", "exited"), name
        labels = info["Config"]["Labels"]
        assert {k: v for k, v in labels.items() if k.startswith("oncallpilot.")} == {
            "oncallpilot.managed": "true",
            "oncallpilot.service": service,
            "oncallpilot.release": release,
            "oncallpilot.replica": replica,
            "oncallpilot.logs": "true",
        }, name
        network = info["NetworkSettings"]["Networks"][NETWORK]
        assert f"{service}-upstream" in (network.get("Aliases") or []), name


def test_api_metrics_follow_the_contract() -> None:
    text = fetch("http://api-upstream:8000/metrics")
    base = {"service": "api", "release": "1.4.0", "instance": "cs-api-140-1"}
    assert metric_problems(text, API_METRICS, base, require_process=True) == []


def test_worker_metrics_follow_the_contract() -> None:
    text = fetch("http://worker-upstream:8001/metrics")
    base = {"service": "worker", "release": "2.1.0", "instance": "cs-worker-210"}
    assert metric_problems(text, WORKER_METRICS, base, require_process=True) == []


@pytest.mark.parametrize("container", sorted(JSON_LOGGERS))
def test_log_lines_follow_the_contract(container: str) -> None:
    result = subprocess.run(
        ["docker", "logs", "--tail", "1000", container],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    # C7 lines go to stdout. Only cs-lb writes to stderr: nginx's plain-text error log.
    if container != "cs-lb":
        assert result.stderr == "", f"{container} wrote to stderr"
    lines = [line for line in result.stdout.splitlines() if line]
    assert lines, f"{container} wrote no log lines"
    base = {"service": JSON_LOGGERS[container], "instance": container}
    problems = {line[:120]: p for line in lines if (p := log_problems(line, base))}
    assert problems == {}


def test_baseline_traffic_is_five_requests_per_second() -> None:
    """TB-005 acceptance, measured on cs-api's counter instead of Prometheus (B1.5)."""
    first = fetch("http://api-upstream:8000/metrics")
    started = time.monotonic()
    time.sleep(60)
    second = fetch("http://api-upstream:8000/metrics")
    elapsed = time.monotonic() - started
    loadgen_rps = (
        request_total(second, include_healthz=False) - request_total(first, include_healthz=False)
    ) / elapsed
    all_rps = (
        request_total(second, include_healthz=True) - request_total(first, include_healthz=True)
    ) / elapsed
    print(f"\nloadgen traffic {loadgen_rps:.2f} rps, all requests {all_rps:.2f} rps")
    assert 4.0 <= all_rps <= 6.0
    assert 4.0 <= loadgen_rps <= 6.0


def send_through_lb(count: int) -> list[str]:
    """Send GET /products through cs-lb with our own request ids; return the ids."""
    ids = []
    for _ in range(count):
        request_id = secrets.token_hex(16)
        request = urllib.request.Request(f"{LB_URL}/products", headers={"X-Request-ID": request_id})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
        ids.append(request_id)
    return ids


def served_by(container: str, ids: list[str], since: str) -> int:
    logs = docker("logs", "--since", since, container)
    return sum(1 for request_id in ids if f'"request_id":"{request_id}"' in logs)


def started_at(container: str) -> float:
    raw = docker("inspect", "--format", "{{.State.StartedAt}}", container).strip()
    return parse_ts(raw)


def parse_ts(raw: str) -> float:
    """RFC 3339 with up to nanoseconds and Z -> epoch seconds."""
    head, _, frac = raw.rstrip("Z").partition(".")
    seconds = datetime.fromisoformat(head).replace(tzinfo=UTC).timestamp()
    return seconds + float(f"0.{frac or 0}")


def app_ready(container: str) -> float | None:
    """Time the app finished starting, since the container last started."""
    since = docker("inspect", "--format", "{{.State.StartedAt}}", container).strip()
    for line in docker("logs", "--since", since, container).splitlines():
        if "Application startup complete" in line:
            return parse_ts(json.loads(line)["ts"])
    return None


def first_served(container: str) -> float | None:
    """Time of the first /products request served since the container last started."""
    since = docker("inspect", "--format", "{{.State.StartedAt}}", container).strip()
    for line in docker("logs", "--since", since, container).splitlines():
        if '"route":"/products"' in line:
            return parse_ts(json.loads(line)["ts"])
    return None


def test_lb_follows_scale_up_and_down() -> None:
    """TB-011: scale 1 -> 3 -> 1; cs-lb routes to every running slot within 10 s."""
    extra = ["cs-api-140-2", "cs-api-140-3"]
    replicas = ["cs-api-140-1", *extra]
    since = docker("info", "--format", "{{json .SystemTime}}").strip().strip('"')
    try:
        docker("start", *extra)
        # Keep traffic flowing until every new slot has served a request.
        deadline = time.monotonic() + 3 * TB_011_SECONDS
        while time.monotonic() < deadline and not all(first_served(c) for c in extra):
            send_through_lb(6)
        # Measured on the container's own clock. TB-011 is about cs-lb: a slot
        # whose app is listening must be served within 10 s. The boot time
        # before that (Python at cpus 0.5) is printed, not asserted.
        for name in extra:
            started, ready = started_at(name), app_ready(name)
            served = first_served(name)
            assert ready is not None and served is not None, f"{name} never served"
            print(
                f"{name}: app ready {ready - started:.1f} s after start, "
                f"served by cs-lb {served - ready:.1f} s after ready"
            )
            assert served - ready <= TB_011_SECONDS, name
        ids = send_through_lb(30)
        counts = {c: served_by(c, ids, since) for c in replicas}
        print(f"30 requests split {counts}")
        assert all(counts.values()) and sum(counts.values()) == 30
    finally:
        docker("stop", "-t", "10", *extra)
    # During the next 10 s a request that nginx sends to a just-stopped slot can
    # fail (504 after the connect timeout; nginx does not retry it). Count them.
    window_end = time.monotonic() + TB_011_SECONDS
    during = failed = 0
    while time.monotonic() < window_end:
        during += 1
        try:
            send_through_lb(1)
        except urllib.error.HTTPError:
            failed += 1
    # After 10 s nginx must no longer try the stopped slots at all.
    after = docker("info", "--format", "{{json .SystemTime}}").strip().strip('"')
    ids = send_through_lb(20)
    assert served_by("cs-api-140-1", ids, after) == 20
    lb_errors = subprocess.run(
        ["docker", "logs", "--since", after, "cs-lb"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    ).stderr
    assert "connect() failed" not in lb_errors, lb_errors
    print(
        f"scale-down: {failed} of {during} requests failed in the 10 s window; then 20/20 on 140-1"
    )
