"""TB-002 slots and TB-011 cs-lb configuration, checked statically (no Docker needed).

The live behaviour (slots start and stop, cs-lb follows within 10 s) is in
tests/live/test_live.py.
"""

import re
from pathlib import Path
from typing import Any

import yaml

from common.identity import commit_for

SHOP = Path(__file__).resolve().parents[1]
RELEASES = SHOP / "releases.yaml"
TESTBED = SHOP.parent / "infrastructure" / "compose" / "testbed.yml"
NGINX = SHOP / "lb" / "nginx.conf"

API_SLOTS = [f"cs-api-{r}-{n}" for r in ("140", "150") for n in range(1, 6)]
WORKER_SLOTS = ["cs-worker-210", "cs-worker-220"]
BASELINE = {"cs-api-140-1", "cs-worker-210"}
SLOT_NAME = re.compile(r"^cs-(api|worker)-(\d)(\d)(\d)(?:-(\d))?$")


def services() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(TESTBED.read_text(encoding="utf-8"))["services"]
    return data


def aliases(service: dict[str, Any]) -> list[str]:
    networks = service.get("networks", {})
    if not isinstance(networks, dict):
        return []
    return list((networks.get("chaos_net") or {}).get("aliases", []))


def test_every_slot_exists_exactly_once() -> None:
    slots = {name for name in services() if SLOT_NAME.match(name)}
    assert slots == set(API_SLOTS + WORKER_SLOTS)


def test_slot_labels_follow_the_container_name() -> None:
    for name in API_SLOTS + WORKER_SLOTS:
        service = services()[name]
        match = SLOT_NAME.match(name)
        assert match is not None
        kind, major, minor, patch, replica = match.groups()
        release = f"{major}.{minor}.{patch}"
        labels = service["labels"]
        assert labels["oncallpilot.service"] == kind, name
        assert labels["oncallpilot.release"] == release, name
        assert labels["oncallpilot.replica"] == (replica or "1"), name
        assert service["image"] == f"chaos-shop/{kind}:{release}", name
        assert service["environment"]["CS_INSTANCE"] == name


def test_slot_aliases_ports_and_limits() -> None:
    for name in API_SLOTS:
        service = services()[name]
        assert aliases(service) == ["api-upstream"], name
        assert service["cpus"] == 0.5, name
        assert "restart" not in service, name
    for name in WORKER_SLOTS:
        service = services()[name]
        assert aliases(service) == ["worker-upstream"], name
        assert service["restart"] == "on-failure", name
    for name, service in services().items():
        assert service.get("mem_limit"), f"{name} has no memory limit"
        if name != "cs-lb":
            assert "ports" not in service, f"{name} publishes ports"


def test_only_the_baseline_starts_with_compose() -> None:
    for name in API_SLOTS + WORKER_SLOTS:
        expected = ["testbed"] if name in BASELINE else ["testbed-slots"]
        assert services()[name]["profiles"] == expected, name
        if name not in BASELINE:
            # Slots are started by the runner or the chaos CLI, never by Compose.
            assert "depends_on" not in services()[name], name


def test_every_slot_image_has_a_release_and_a_build() -> None:
    images: dict[str, list[str]] = {}
    for name, service in services().items():
        if service["image"].startswith("chaos-shop/"):
            images.setdefault(service["image"], []).append(name)
    builders = {s["image"] for s in services().values() if "build" in s}
    assert set(images) <= builders, "an image has no service that builds it"
    for image in images:
        kind, release = image.removeprefix("chaos-shop/").split(":")
        if kind in ("api", "worker"):
            assert len(commit_for(RELEASES, kind, release)) == 12


def test_releases_match_the_slots() -> None:
    data = yaml.safe_load(RELEASES.read_text(encoding="utf-8"))["releases"]
    assert set(data["api"]) == {"1.4.0", "1.5.0"}
    assert set(data["worker"]) == {"2.1.0", "2.2.0"}
    shas = [entry["commit_sha"] for releases in data.values() for entry in releases.values()]
    assert len(set(shas)) == len(shas)


def test_lb_is_loopback_only_and_labelled() -> None:
    lb = services()["cs-lb"]
    assert lb["ports"] == ["127.0.0.1:8080:80"]
    assert lb["image"] == "nginx:1.30.5-alpine"
    assert lb["labels"]["oncallpilot.service"] == "lb"
    assert lb["labels"]["oncallpilot.release"] == "1.30.5"
    assert services()["cs-loadgen"]["environment"]["CS_LOADGEN_TARGET_URL"] == "http://cs-lb"


def test_nginx_follows_the_alias_without_reload() -> None:
    """ADR-07 and the S0.7(b) spike settings."""
    conf = NGINX.read_text(encoding="utf-8")
    assert "resolver 127.0.0.11 valid=5s ipv6=off;" in conf
    assert re.search(
        r"upstream api \{\s*zone api 64k;\s*"
        r"server api-upstream:8000 resolve max_fails=1 fail_timeout=2s;",
        conf,
    )
    assert "proxy_next_upstream error timeout;" in conf


def test_nginx_access_log_has_the_c7_fields() -> None:
    conf = NGINX.read_text(encoding="utf-8")
    log_format = conf[conf.index("log_format c7") : conf.index("access_log /dev/stdout c7;")]
    assert "escape=json" in log_format
    for field in (
        '"ts":',
        '"level":',
        '"service":"lb"',
        '"release":"1.30.5"',
        '"instance":"cs-lb"',
        '"logger":"nginx.access"',
        '"msg":"$request_method $uri $status"',
        '"request_id":',
        '"status":$status',
        '"duration_ms":',
    ):
        assert field in log_format, field
    assert "proxy_set_header X-Request-ID $c7_request_id;" in conf
