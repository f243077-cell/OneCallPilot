"""Live checks against the running testbed (TB-001, TB-003, TB-004, TB-005).

Run from chaos-shop/ on the host, with the testbed up and running for at least 2 minutes:

    docker compose --profile testbed up -d --wait      # from the repository root
    uv run pytest -m live -v

Nothing is published on the host, so /metrics is fetched by a throwaway
python:3.12.15-slim container on the testbed network.
"""

import json
import subprocess
import time

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
    "cs-redis": ("redis", "7.4.11"),
    "cs-payments": ("payments", "1.0.0"),
    "cs-api-140-1": ("api", "1.4.0"),
    "cs-worker-210": ("worker", "2.1.0"),
}
JSON_LOGGERS = {"cs-api-140-1": "api", "cs-worker-210": "worker", "cs-payments": "payments"}


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
    for name in [*MANAGED, "cs-loadgen"]:
        ports = json.loads(docker("inspect", "--format", "{{json .HostConfig.PortBindings}}", name))
        assert not ports, f"{name} publishes {ports}"


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
    lines = [line for line in (result.stdout + result.stderr).splitlines() if line]
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
