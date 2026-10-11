"""The read-only Docker view (docker_client.py) and the store's admin client
(shop_admin.py), without a real Docker or cs-lb."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from docker.errors import APIError

from oncallpilot_runner.docker_client import Container, DockerUnavailable, SdkDocker
from oncallpilot_runner.health import http_probe
from oncallpilot_runner.shop_admin import CacheStatsUnavailable, fetch_cache_stats


class StubContainers:
    def __init__(self, attrs: list[dict[str, Any]], error: Exception | None = None) -> None:
        self.attrs, self.error = attrs, error
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> list[SimpleNamespace]:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return [SimpleNamespace(attrs=a) for a in self.attrs]


def reader(attrs: list[dict[str, Any]], error: Exception | None = None) -> SdkDocker:
    return SdkDocker(SimpleNamespace(containers=StubContainers(attrs, error)))


def test_lists_every_container_with_all_labels_and_returns_plain_values() -> None:
    r = reader([
        {
            "Name": "/cs-api-140-1",
            "Config": {"Labels": {"oncallpilot.service": "api"}},
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
        },
        {"Name": "/cs-redis", "Config": {"Labels": None}, "State": {"Status": "exited"}},
    ])  # fmt: skip
    found = r.list_containers({"oncallpilot.service": "api", "oncallpilot.managed": "true"})
    assert found == [
        Container("cs-api-140-1", {"oncallpilot.service": "api"}, "running", "healthy"),
        Container("cs-redis", {}, "exited", "none"),
    ]
    assert r.client.containers.calls == [
        {"all": True, "filters": {"label": ["oncallpilot.managed=true", "oncallpilot.service=api"]}}
    ]


@pytest.mark.parametrize(
    "error",
    [APIError("403 Forbidden"), requests.exceptions.ConnectionError("refused")],
)
def test_docker_errors_become_docker_unavailable(error: Exception) -> None:
    with pytest.raises(DockerUnavailable, match="Docker did not answer"):
        reader([], error).list_containers({"oncallpilot.service": "api"})


def test_connect_pins_the_api_version_and_makes_no_call() -> None:
    """The proxy blocks /version, so the SDK must never negotiate it."""
    r = SdkDocker.connect("tcp://127.0.0.1:9", "1.48")  # nothing listens on port 9
    assert r.client.api._version == "1.48"
    assert r.client.api.base_url == "http://127.0.0.1:9"


# --- shop_admin -----------------------------------------------------------------------

TOKEN = "t" * 32


class Shop(BaseHTTPRequestHandler):
    reply: tuple[int, bytes] = (200, b"{}")
    seen: list[dict[str, str]] = []

    def do_GET(self) -> None:  # noqa: N802 (http.server's name)
        Shop.seen.append({"path": self.path, "auth": self.headers.get("Authorization", "")})
        status, body = Shop.reply
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture
def shop() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), Shop)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Shop.seen = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_cache_stats_are_read_with_the_bearer_token(shop: str) -> None:
    stats = {"catalog": {"present": True, "ttl_seconds": 3}, "pricing": {"entries": 4}}
    Shop.reply = (200, json.dumps(stats).encode())
    assert fetch_cache_stats(TOKEN, shop) == stats
    assert Shop.seen == [{"path": "/internal/cache/stats", "auth": f"Bearer {TOKEN}"}]


@pytest.mark.parametrize(
    ("reply", "message"),
    [((401, b'{"error":"UNAUTHORIZED"}'), "HTTP 401"), ((200, b"[1]"), "not a JSON object"),
     ((200, b"not json"), "JSONDecodeError")],
)  # fmt: skip
def test_bad_answers_are_cache_stats_unavailable(
    shop: str, reply: tuple[int, bytes], message: str
) -> None:
    Shop.reply = reply
    with pytest.raises(CacheStatsUnavailable, match=message):
        fetch_cache_stats(TOKEN, shop)


def test_an_unreachable_shop_is_cache_stats_unavailable() -> None:
    with pytest.raises(CacheStatsUnavailable):
        fetch_cache_stats(TOKEN, "http://127.0.0.1:9")


class StubApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def inspect_container(self, name: str) -> dict[str, Any]:
        self.calls.append(("inspect", name))
        return {"Id": "abc", "Name": f"/{name}", "RestartCount": 2,
                "Config": {"Labels": {}}, "State": {"Status": "running"}}  # fmt: skip

    def start(self, container_id: str) -> None:
        self.calls.append(("start", container_id))

    def stop(self, container_id: str, timeout: int) -> None:
        self.calls.append(("stop", (container_id, timeout)))


def test_inspect_start_and_stop_go_by_id_with_a_graceful_timeout() -> None:
    api = StubApi()
    sdk = SdkDocker(SimpleNamespace(api=api))
    c = sdk.inspect("cs-api-140-1")
    assert (c.id, c.name, c.restart_count, c.health) == ("abc", "cs-api-140-1", 2, "none")
    sdk.start(c.id)
    sdk.stop(c.id)
    assert api.calls == [("inspect", "cs-api-140-1"), ("start", "abc"), ("stop", ("abc", 10))]


def test_a_refused_write_is_docker_unavailable() -> None:
    class Refusing(StubApi):
        def stop(self, container_id: str, timeout: int) -> None:
            raise APIError("403 Forbidden")

    with pytest.raises(DockerUnavailable):
        SdkDocker(SimpleNamespace(api=Refusing())).stop("abc")


def test_http_probe_reports_status_latency_and_errors(shop: str) -> None:
    Shop.reply = (200, b"{}")
    ok = http_probe(f"{shop}/healthz")
    assert (ok["ok"], ok["status_code"], ok["error"]) == (True, 200, None)
    assert ok["latency_ms"] is not None and ok["latency_ms"] >= 0
    Shop.reply = (503, b"{}")
    bad = http_probe(f"{shop}/healthz")
    assert (bad["ok"], bad["status_code"], bad["error"]) == (False, 503, "HTTP 503")
    down = http_probe("http://127.0.0.1:9/healthz")
    assert (down["ok"], down["status_code"], down["latency_ms"]) == (False, None, None)
