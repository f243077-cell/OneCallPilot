# ADR-21 — socket-proxy-ro also allows container logs (`ALLOW_LOGS=1`)

- **Status:** Accepted, 2026-10-21 (cloud session for Tanzeel; please review)
- **Changes:** architecture §2.11 (socket-proxy-ro settings), SEC-014's allowed endpoint list
- **Evidence:** `docs/checks/proxy.md` §(a), 2026-10-21

## Context
Architecture §2.11 gives socket-proxy-ro `CONTAINERS=1`, `NETWORKS=1`, `EVENTS=1`, `PING=1`, `VERSION=1`, `POST=0`, so that Grafana Alloy (`discovery.docker` + `loki.source.docker`) can ship Chaos Shop logs to Loki without the Docker socket (ADR-06, TB-012). Task S0.7 (a) was to prove this before building on it.

The S0.7 (a) run showed that `lscr.io/linuxserver/socket-proxy:3.4.6-r0-ls101` refuses `GET /containers/{id}/logs` with **403** unless `ALLOW_LOGS` is set, even with `CONTAINERS=1`: its haproxy config denies the `logs`, `archive`, `export`, `top` and `changes` sub-resources separately. Discovery worked, but no log line reached Loki.

## Decision
socket-proxy-ro gets `ALLOW_LOGS=1`. Nothing else changes: `POST=0` stays, no other `ALLOW_*` option is set, and `archive`, `export`, `top` and `changes` stay refused. `NETWORKS=1` stays too: the same run showed Alloy's discovery calls `GET /networks` on every refresh.

## Consequences
- Alloy collects logs through the proxy as designed; the Docker socket is still mounted only by the two proxies (`CLAUDE.md` §4.8).
- Every client on `ro_proxy_net` (Alloy, later backend-worker) can read any container's stdout and stderr through the proxy, not only Chaos Shop's. That is read-only, and no OnCallPilot service logs secrets (`CLAUDE.md` §4.3, §6.7); the agent's tools never call this endpoint (`get_service_health` projects fixed fields, AI-008).
- `tests/security/check_ro_proxy.py` (TEST-015 ro half) asserts `logs` is the only container sub-resource allowed and that every write and every other endpoint is refused (52 checks), in CI (`backend.yml`).
- Alternative rejected: a third proxy only for Alloy. More moving parts for the same exposure, since backend-worker is the only other client.
