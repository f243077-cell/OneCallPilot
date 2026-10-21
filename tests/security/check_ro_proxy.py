"""TEST-015 (ro half), SEC-014: socket-proxy-ro answers only reads.

Runs inside a throwaway container on ``ro_proxy_net`` (see
``run_ro_proxy_test.sh``), standard library only. Every write, every
endpoint beyond containers/networks/events/ping/version, and every container
sub-resource other than ``logs`` (ADR-21) must be refused with 403. A safety
test: never skip or loosen it.

Environment: OCP_RO_PROXY (default socket-proxy-ro:2375), OCP_PROXY_TEST_TARGET
(a throwaway container the host created), DOCKER_API_VERSION (e.g. 1.49).
"""

from __future__ import annotations

import http.client
import os
import sys

HOST, _, PORT = os.environ.get("OCP_RO_PROXY", "socket-proxy-ro:2375").partition(":")
TARGET = os.environ["OCP_PROXY_TEST_TARGET"]
V = "/v" + os.environ["DOCKER_API_VERSION"]

ALLOWED = [
    ("GET", "/_ping"),
    ("GET", "/version"),
    ("GET", f"{V}/containers/json"),
    ("GET", f"{V}/containers/json?all=1"),
    ("GET", f"{V}/containers/{TARGET}/json"),
    ("GET", f"{V}/containers/{TARGET}/logs?stdout=1&stderr=1&tail=1"),
    ("GET", f"{V}/networks"),
    ("GET", f"{V}/events?since=0&until=1"),
]

REFUSED = [
    # writes to containers
    ("POST", f"{V}/containers/create"),
    ("POST", "/containers/create"),
    ("POST", f"{V}/containers/{TARGET}/start"),
    ("POST", f"{V}/containers/{TARGET}/stop"),
    ("POST", f"{V}/containers/{TARGET}/restart"),
    ("POST", f"{V}/containers/{TARGET}/kill"),
    ("POST", f"{V}/containers/{TARGET}/pause"),
    ("POST", f"{V}/containers/{TARGET}/update"),
    ("POST", f"{V}/containers/{TARGET}/rename?name=x"),
    ("POST", f"{V}/containers/{TARGET}/exec"),
    ("POST", f"{V}/containers/{TARGET}/attach"),
    ("POST", f"{V}/containers/prune"),
    ("DELETE", f"{V}/containers/{TARGET}"),
    ("PUT", f"{V}/containers/{TARGET}/archive?path=/tmp"),
    # container reads other than logs
    ("GET", f"{V}/containers/{TARGET}/archive?path=/"),
    ("GET", f"{V}/containers/{TARGET}/export"),
    ("GET", f"{V}/containers/{TARGET}/top"),
    ("GET", f"{V}/containers/{TARGET}/changes"),
    # beyond containers
    ("GET", f"{V}/images/json"),
    ("POST", f"{V}/images/create?fromImage=busybox&tag=1.37"),
    ("POST", f"{V}/build"),
    ("GET", f"{V}/volumes"),
    ("POST", f"{V}/volumes/create"),
    ("POST", f"{V}/networks/create"),
    ("DELETE", f"{V}/networks/ocp_x"),
    ("GET", f"{V}/info"),
    ("GET", f"{V}/exec/abc/json"),
    ("POST", f"{V}/exec/abc/start"),
    ("GET", f"{V}/secrets"),
    ("GET", f"{V}/configs"),
    ("GET", f"{V}/services"),
    ("GET", f"{V}/system/df"),
    ("POST", f"{V}/auth"),
    ("POST", f"{V}/commit?container={TARGET}"),
    ("GET", f"{V}/plugins"),
    ("GET", f"{V}/swarm"),
    ("GET", f"{V}/nodes"),
    ("GET", f"{V}/tasks"),
    ("GET", f"{V}/distribution/busybox/json"),
    # Podman twins
    ("GET", "/libpod/_ping"),
    ("GET", "/libpod/version"),
    ("GET", f"{V}/libpod/containers/json"),
]

# Docker answers these with a 301 to the cleaned path (/images/json); a client
# that follows it must get 403 from the proxy, never the image list.
PATH_TRICKS = [
    ("GET", f"{V}/containers/../images/json"),
    ("GET", f"{V}/containers/%2e%2e/images/json"),
]


def status(method: str, path: str, follow: bool = False) -> int:
    conn = http.client.HTTPConnection(HOST, int(PORT or 2375), timeout=10)
    try:
        body = "{}" if method in ("POST", "PUT") else None
        headers = {"Content-Type": "application/json"} if body else {}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        location = response.getheader("Location")
        if follow and response.status in (301, 302, 307, 308) and location:
            return status(method, location)
        return response.status
    finally:
        conn.close()


def main() -> int:
    failures = []
    for method, path in ALLOWED:
        got = status(method, path)
        ok = got == 200
        print(f"{'ok ' if ok else 'BAD'} {got} allowed {method} {path}")
        if not ok:
            failures.append((method, path, got))
    for method, path in REFUSED:
        got = status(method, path)
        ok = got == 403
        print(f"{'ok ' if ok else 'BAD'} {got} refused {method} {path}")
        if not ok:
            failures.append((method, path, got))
    for method, path in PATH_TRICKS:
        got = status(method, path, follow=True)
        ok = got == 403
        print(f"{'ok ' if ok else 'BAD'} {got} refused (after redirect) {method} {path}")
        if not ok:
            failures.append((method, path, got))
    total = len(ALLOWED) + len(REFUSED) + len(PATH_TRICKS)
    print(f"{total - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
