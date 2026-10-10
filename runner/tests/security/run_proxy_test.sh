#!/usr/bin/env bash
# TEST-015 / RUN-017: run tests/security/test_proxy.py against the REAL socket-proxy-rw.
#
# Usage (from the repository root, Git Bash or Linux):
#   DOCKER_API_VERSION=<pinned value from docs/checks/proxy.md> runner/tests/security/run_proxy_test.sh
# If DOCKER_API_VERSION is unset it is read from the host's `docker version`
# (the host talks to the real socket; only the runner must pin it). The CI job
# relies on this.
#
# What it does:
#   1. starts socket-proxy-rw (compose profile `runner`) and waits until it is healthy;
#   2. creates a throwaway target container with the host's Docker (never through the proxy);
#   3. runs pytest -m proxy in a python:3.12-slim container attached to the default
#      bridge (to install dependencies) and to the internal rw_proxy_net (to reach the proxy);
#   4. removes the target and the test container, and stops the proxy if this script started it.
set -euo pipefail

: "${DOCKER_API_VERSION:=$(docker version --format '{{.Server.APIVersion}}')}"
: "${DOCKER_API_VERSION:?could not determine the Docker API version; set it (docs/checks/proxy.md)}"

export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && (pwd -W 2>/dev/null || pwd))"
RUNNER_DIR="$ROOT/runner"
NETWORK="oncallpilot_rw_proxy_net"
TARGET="ocp-proxytest-target"
TESTER="ocp-proxytest-runner"
UV_VERSION="0.12.7"

proxy_was_running="$(docker compose -f "$ROOT/compose.yaml" --profile runner ps -q socket-proxy-rw)"

cleanup() {
  docker rm -f "$TESTER" "$TARGET" >/dev/null 2>&1 || true
  if [ -z "$proxy_was_running" ]; then
    docker compose -f "$ROOT/compose.yaml" --profile runner stop socket-proxy-rw >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

docker compose -f "$ROOT/compose.yaml" --profile runner up -d --wait socket-proxy-rw
docker rm -f "$TESTER" "$TARGET" >/dev/null 2>&1 || true
docker run -d --name "$TARGET" --label oncallpilot.test=proxy busybox:1.37 sleep 3600 >/dev/null

docker create --name "$TESTER" \
  -e DOCKER_API_VERSION="$DOCKER_API_VERSION" \
  -e OCP_RW_PROXY_URL="tcp://socket-proxy-rw:2375" \
  -e OCP_PROXY_TEST_TARGET="$TARGET" \
  -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
  -e UV_CACHE_DIR=/tmp/uv-cache \
  -v "$RUNNER_DIR:/src:ro" -w /src \
  python:3.12-slim \
  sh -c "pip install --quiet --disable-pip-version-check uv==$UV_VERSION \
         && uv sync --locked --quiet \
         && uv run --no-sync pytest -m proxy -v -p no:cacheprovider tests/security/test_proxy.py" >/dev/null
docker network connect "$NETWORK" "$TESTER"
docker start -a "$TESTER"
