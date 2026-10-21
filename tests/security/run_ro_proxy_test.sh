#!/usr/bin/env bash
# TEST-015 (ro half), SEC-014: run tests/security/check_ro_proxy.py against the REAL
# socket-proxy-ro, from a throwaway container on the internal ro_proxy_net.
#
# Usage (repository root, Linux or Git Bash; needs .env, e.g. a copy of .env.example):
#   tests/security/run_ro_proxy_test.sh
# Extra compose files (for example a local override) can be passed in COMPOSE_FILES.
set -euo pipefail

: "${DOCKER_API_VERSION:=$(docker version --format '{{.Server.APIVersion}}')}"
export MSYS_NO_PATHCONV=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && (pwd -W 2>/dev/null || pwd))"
COMPOSE=(docker compose -f "$ROOT/compose.yaml" ${COMPOSE_FILES:-})
NETWORK="oncallpilot_ro_proxy_net"
TARGET="ocp-roproxytest-target"
TESTER="ocp-roproxytest-client"

was_running="$("${COMPOSE[@]}" --profile obs ps -q socket-proxy-ro)"
cleanup() {
  docker rm -f "$TESTER" "$TARGET" >/dev/null 2>&1 || true
  if [ -z "$was_running" ]; then
    "${COMPOSE[@]}" --profile obs stop socket-proxy-ro >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

"${COMPOSE[@]}" --profile obs up -d --wait socket-proxy-ro
docker rm -f "$TESTER" "$TARGET" >/dev/null 2>&1 || true
docker run -d --name "$TARGET" --label oncallpilot.test=ro-proxy busybox:1.37 \
  sh -c 'echo hello; sleep 3600' >/dev/null
docker create --name "$TESTER" --network "$NETWORK" \
  -e OCP_PROXY_TEST_TARGET="$TARGET" -e DOCKER_API_VERSION="$DOCKER_API_VERSION" \
  -v "$ROOT/tests/security/check_ro_proxy.py:/check.py:ro" \
  python:3.12.15-slim python /check.py >/dev/null
docker start -a "$TESTER"
