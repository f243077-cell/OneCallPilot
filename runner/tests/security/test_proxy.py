"""TEST-015 and RUN-017: socket-proxy-rw allows only container reads, start, stop,
restart and kill. Everything that creates, executes in, deletes, or reaches beyond
containers is refused with 403, and the Docker socket is unreachable except through
the proxy.

Runs against the REAL proxy from a container on ``rw_proxy_net``, through
``tests/security/run_proxy_test.sh`` (host) or the ``runner-proxy`` CI job. It is a
safety test: never skip or loosen it.

Environment:
    DOCKER_API_VERSION     pinned Docker API version (docs/checks/proxy.md). Required.
    OCP_RW_PROXY_URL       proxy address, default ``tcp://socket-proxy-rw:2375``.
    OCP_PROXY_TEST_TARGET  name of a throwaway container created on the host beforehand.
"""

import base64
import http.client
import os
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import docker
import pytest
import requests
from docker.errors import APIError, DockerException

pytestmark = pytest.mark.proxy

TEST_IMAGE = "busybox:1.37"


def proxy_host_port() -> tuple[str, int]:
    url = urlparse(os.environ.get("OCP_RW_PROXY_URL", "tcp://socket-proxy-rw:2375"))
    return url.hostname or "socket-proxy-rw", url.port or 2375


@pytest.fixture(scope="module")
def api() -> docker.APIClient:
    # An explicit version stops the SDK from calling GET /version, which the proxy blocks.
    return docker.APIClient(
        base_url=os.environ.get("OCP_RW_PROXY_URL", "tcp://socket-proxy-rw:2375"),
        version=os.environ["DOCKER_API_VERSION"],
        timeout=30,
    )


@pytest.fixture(scope="module")
def target() -> str:
    return os.environ["OCP_PROXY_TEST_TARGET"]


@pytest.fixture(scope="module")
def ver() -> str:
    return os.environ["DOCKER_API_VERSION"]


def raw(method: str, path: str, body: str | None = None) -> int:
    """One request straight to the proxy; returns the HTTP status (no SDK in the way)."""
    host, port = proxy_host_port()
    conn = http.client.HTTPConnection(host, port, timeout=15)
    try:
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=body, headers=headers)
        return conn.getresponse().status
    finally:
        conn.close()


def assert_forbidden(call: Callable[[], Any]) -> None:
    with pytest.raises(APIError) as excinfo:
        call()
    assert excinfo.value.status_code == 403, excinfo.value


def is_running(api: docker.APIClient, name: str) -> bool:
    return bool(api.inspect_container(name)["State"]["Running"])


# --- Allowed: read containers, start, stop, restart, kill (RUN-017, architecture §2.10) ---


def test_list_and_inspect_containers_allowed(api: docker.APIClient, target: str) -> None:
    names = {name for c in api.containers(all=True) for name in c["Names"]}
    assert f"/{target}" in names
    assert api.inspect_container(target)["Name"] == f"/{target}"


def test_stop_start_restart_allowed(api: docker.APIClient, target: str) -> None:
    api.stop(target, timeout=5)
    assert not is_running(api, target)
    api.start(target)
    assert is_running(api, target)
    api.restart(target, timeout=5)
    assert is_running(api, target)


def test_kill_allowed(api: docker.APIClient, target: str, ver: str) -> None:
    # §2.10 lists kill as allowed; the image groups it with ALLOW_RESTARTS. The runner
    # does not use it, but it is in the documented allow-list, so it must still work.
    assert raw("POST", f"/v{ver}/containers/{target}/kill?signal=SIGCONT") == 204
    if not is_running(api, target):
        api.start(target)


# --- Forbidden: create, exec, delete, images, and everything beyond containers ---


def test_create_container_forbidden(api: docker.APIClient) -> None:
    assert_forbidden(lambda: api.create_container(TEST_IMAGE, "true"))


def test_create_privileged_container_forbidden(api: docker.APIClient) -> None:
    host_config = api.create_host_config(privileged=True, binds=["/:/host"])
    assert_forbidden(lambda: api.create_container(TEST_IMAGE, "true", host_config=host_config))


def test_exec_forbidden(api: docker.APIClient, target: str) -> None:
    assert_forbidden(lambda: api.exec_create(target, ["true"]))


def test_delete_container_forbidden(api: docker.APIClient, target: str) -> None:
    assert_forbidden(lambda: api.remove_container(target, force=True))


def test_image_pull_forbidden(api: docker.APIClient) -> None:
    assert_forbidden(lambda: api.pull("busybox", tag="1.37"))


def test_images_list_forbidden(api: docker.APIClient) -> None:
    assert_forbidden(lambda: api.images())


def test_network_create_forbidden(api: docker.APIClient) -> None:
    assert_forbidden(lambda: api.create_network("ocp-proxytest-net"))


def test_volume_create_forbidden(api: docker.APIClient) -> None:
    assert_forbidden(lambda: api.create_volume("ocp-proxytest-vol"))


def test_container_update_forbidden(api: docker.APIClient, target: str) -> None:
    assert_forbidden(lambda: api.update_container(target, mem_limit="64m"))


def test_version_and_ping_forbidden(api: docker.APIClient) -> None:
    assert_forbidden(lambda: api.version())
    assert_forbidden(lambda: api.ping())


def test_events_forbidden(api: docker.APIClient) -> None:
    # The SDK's event stream turns HTTP errors into StopIteration, so check the raw status.
    response = api.get(f"{api.base_url}/v{api.api_version}/events", stream=True, timeout=10)
    try:
        assert response.status_code == 403
    finally:
        response.close()


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("POST", "pause"),
        ("POST", "unpause"),
        ("POST", "rename?name=evil"),
        ("POST", "wait"),
        ("POST", "resize?h=1&w=1"),
        ("POST", "attach?stream=1&stdin=1"),
        ("POST", "update"),
    ],
)
def test_container_write_verbs_forbidden(target: str, ver: str, method: str, suffix: str) -> None:
    """Every POST to a container other than start/stop/restart/kill is 403 (POST=0)."""
    assert raw(method, f"/v{ver}/containers/{target}/{suffix}") == 403


@pytest.mark.parametrize(
    "path",
    [
        "containers/prune",
        "build",
        "commit?container=x",
        "session",
        "auth",
        "exec/abc/start",
    ],
)
def test_other_write_endpoints_forbidden(ver: str, path: str) -> None:
    assert raw("POST", f"/v{ver}/{path}") == 403


@pytest.mark.parametrize(
    "path",
    [
        "info",
        "system/df",
        "secrets",
        "services",
        "nodes",
        "swarm",
        "plugins",
        "tasks",
        "configs",
        "distribution/busybox:1.37/json",
        "networks",
        "volumes",
        "images/json",
        "libpod/containers/json",
    ],
)
def test_read_endpoints_beyond_containers_forbidden(ver: str, path: str) -> None:
    assert raw("GET", f"/v{ver}/{path}") == 403


@pytest.mark.parametrize("suffix", ["logs?stdout=1", "export", "top", "changes"])
def test_container_reads_disabled_by_default_forbidden(target: str, ver: str, suffix: str) -> None:
    """logs, archive, export, top and changes are gated on their own ALLOW_* (all 0)."""
    assert raw("GET", f"/v{ver}/containers/{target}/{suffix}") == 403


@pytest.mark.parametrize("method", ["GET", "HEAD", "PUT"])
def test_container_archive_forbidden(target: str, ver: str, method: str) -> None:
    # archive is file copy into/out of a container; denied for read and write.
    assert raw(method, f"/v{ver}/containers/{target}/archive?path=/etc/hostname") == 403


@pytest.mark.parametrize(
    "path",
    [
        "/v{v}//containers/{t}/archive?path=/etc/hostname",  # double slash
        "/v{v}/containers/{t}/%61rchive?path=/etc/hostname",  # url-encoded 'a'
        "/v{v}/containers/{t}/archive%3Fx?path=/etc/hostname",  # encoded '?'
    ],
)
def test_path_tricks_do_not_bypass_the_deny(target: str, ver: str, path: str) -> None:
    assert raw("GET", path.format(v=ver, t=target)) == 403


def test_container_stdio_hijack_is_a_known_limitation(target: str, ver: str) -> None:
    """GET /containers/{id}/attach/ws is a WebSocket stdio hijack. It is reachable
    (HTTP 101) because the proxy filters by endpoint prefix and method only, and the
    architecture allows "GET /containers/*" (§2.10); it cannot be blocked without also
    blocking inspect. Recorded as a known limitation (docs/checks/proxy.md,
    architecture §7.12): our containers' PID 1 do not execute stdin, so no command
    runs, but the capability exceeds start/stop/restart. This test documents the fact;
    change it only when the proxy or the design changes.
    """
    host, port = proxy_host_port()
    conn = socket.create_connection((host, port), timeout=10)
    try:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET /v{ver}/containers/{target}/attach/ws?stream=0&logs=1&stdout=1 HTTP/1.1\r\n"
            f"Host: proxy\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        conn.sendall(request.encode())
        status_line = b""
        while b"\r\n" not in status_line:
            chunk = conn.recv(256)
            if not chunk:
                break
            status_line += chunk
    finally:
        conn.close()
    assert b" 101 " in status_line, f"attach/ws behaviour changed: {status_line!r}"


# --- The Docker socket is unreachable except through the proxy (RUN-016, §2.11) ---


def test_docker_socket_is_not_mounted_in_this_container() -> None:
    assert not os.path.exists("/var/run/docker.sock")
    assert not os.path.exists("/run/docker.sock")


def test_default_docker_client_cannot_reach_a_socket() -> None:
    # No DOCKER_HOST and no mounted socket: a plain client has nothing to talk to.
    # A missing socket surfaces as requests.ConnectionError; a set DOCKER_HOST
    # pointing at the proxy would 403 as an APIError. Either way it must fail.
    with pytest.raises((DockerException, requests.exceptions.RequestException, OSError)):
        docker.from_env(version=os.environ["DOCKER_API_VERSION"]).ping()
