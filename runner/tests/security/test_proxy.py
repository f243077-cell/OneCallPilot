"""TEST-015 and RUN-017: socket-proxy-rw allows only container reads, start, stop and restart.

Runs against the REAL proxy from a container on ``rw_proxy_net``, through
``tests/security/run_proxy_test.sh``. It is a safety test: never skip or loosen it.

Environment:
    DOCKER_API_VERSION     pinned Docker API version (docs/checks/proxy.md). Required.
    OCP_RW_PROXY_URL       proxy address, default ``tcp://socket-proxy-rw:2375``.
    OCP_PROXY_TEST_TARGET  name of a throwaway container created on the host beforehand.
"""

import os
from collections.abc import Callable
from typing import Any

import docker
import pytest
from docker.errors import APIError

pytestmark = pytest.mark.proxy

TEST_IMAGE = "busybox:1.37"


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


def assert_forbidden(call: Callable[[], Any]) -> None:
    with pytest.raises(APIError) as excinfo:
        call()
    assert excinfo.value.status_code == 403, excinfo.value


def is_running(api: docker.APIClient, name: str) -> bool:
    return bool(api.inspect_container(name)["State"]["Running"])


# --- Allowed: read containers, start, stop, restart (RUN-017 acceptance) ---


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


# --- Forbidden: everything that creates, executes, deletes, or reaches beyond containers ---


def test_create_container_forbidden(api: docker.APIClient) -> None:
    assert_forbidden(lambda: api.create_container(TEST_IMAGE, "true"))


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
