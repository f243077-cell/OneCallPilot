# S0.7 — Assumption spike: Docker socket proxies

> **Task:** `phases.md` S0.7 · **Related:** ADR-05, architecture §2.10, §2.11, §3, RUN-017, TEST-015, SEC-014
> Part (a) is split: **socket-proxy-rw** (Tanzeel, below) and **socket-proxy-ro** with Alloy (Usman, his section). Parts (b) nginx `resolve`, (c) embeddings, and (d) Supabase JWKS are recorded in their own sections when run.

## Pinned values

| Item | Value | Used by |
|---|---|---|
| Docker API version (`docker version` → Server API) | **`1.56`** (Docker Engine 29.8.1, Docker Desktop, WSL2) | `DOCKER_API_VERSION` for the runner (`version=` in the Docker SDK) |
| socket-proxy image | `lscr.io/linuxserver/socket-proxy:3.4.6-r0-ls101` (built 2026-10-06; haproxy on Alpine) | `infrastructure/compose/runner.yml` |

If Docker Desktop is upgraded, re-run `docker version`. If the API version changes, update this file and the runner's `DOCKER_API_VERSION`, then re-run the test below.

## (a) socket-proxy-rw — Tanzeel — 2026-10-09

### Finding: image defaults

The image enables **`EVENTS=1`, `PING=1`, `VERSION=1`, `LIBPOD_PING=1`, `LIBPOD_VERSION=1` by default** (`docker image inspect`). The architecture lists only the variables to turn **on**. To meet RUN-017 ("`VERSION` and `PING` stay off"), `runner.yml` sets all five to `0` explicitly. Every other API section defaults to `0` in this image.

> For Usman (socket-proxy-ro): the same defaults apply. `LIBPOD_PING` and `LIBPOD_VERSION` are on unless set to `0`.

### Configuration tested

`infrastructure/compose/runner.yml`, profile `runner`:

`CONTAINERS=1, ALLOW_START=1, ALLOW_STOP=1, ALLOW_RESTARTS=1, POST=0, EVENTS=0, PING=0, VERSION=0, LIBPOD_PING=0, LIBPOD_VERSION=0`, Docker socket mounted read-only, `read_only: true` with `tmpfs: /run`, only on the internal network `rw_proxy_net`, no published ports, healthcheck `nc -z 127.0.0.1 2375`.

Checked on the running container: no published ports, read-only root filesystem, only network `oncallpilot_rw_proxy_net`, and that network is `internal=true`.

### How to run

From the repository root (Git Bash), with Docker Desktop running:

```bash
DOCKER_API_VERSION=1.56 runner/tests/security/run_proxy_test.sh
# Optional: watch every request the proxy decides on
SOCKET_PROXY_RW_LOG_LEVEL=debug DOCKER_API_VERSION=1.56 runner/tests/security/run_proxy_test.sh
docker compose --profile runner logs socket-proxy-rw
```

The script starts the proxy, creates a throwaway `busybox:1.37` target with the host's Docker, and runs `runner/tests/security/test_proxy.py` (`pytest -m proxy`) in a `python:3.12-slim` container on `rw_proxy_net`. The Docker SDK client is created with `version="1.56"`, so it never calls `GET /version`.

### Result: 12/12 passed (S0.7); extended to 54/54 in A2.2 (2026-10-10)

| Request through socket-proxy-rw | Expected | Result |
|---|---|---|
| `GET /containers/json`, `GET /containers/{id}/json`, `/stats` | allowed | ✅ 200 |
| `POST /containers/{id}/stop`, `/start`, `/restart` | allowed | ✅ 204, state changed |
| `POST /containers/{id}/kill` | allowed (§2.10) | ✅ 204 |
| `POST /containers/create` (incl. `Privileged=true`) | 403 | ✅ 403 |
| `POST /containers/{id}/exec`, `/exec/{id}/start` | 403 | ✅ 403 |
| `DELETE /containers/{id}` | 403 | ✅ 403 |
| `POST /containers/{id}/pause` `/unpause` `/rename` `/wait` `/resize` `/attach` `/update` | 403 | ✅ 403 |
| `POST /containers/prune`, `/build`, `/commit`, `/session`, `/auth` | 403 | ✅ 403 |
| `POST /images/create` (pull); `GET /images/json` | 403 | ✅ 403 |
| `POST /networks/create`; `GET /networks`, `/volumes` | 403 | ✅ 403 |
| `GET /info`, `/system/df`, `/secrets`, `/services`, `/nodes`, `/swarm`, `/plugins`, `/tasks`, `/configs`, `/distribution/*` | 403 | ✅ 403 |
| `GET /containers/{id}/logs` `/archive` `/export` `/top` `/changes` (`ALLOW_*`=0) | 403 | ✅ 403 (GET, HEAD, PUT) |
| `GET /libpod/containers/json` (libpod off) | 403 | ✅ 403 |
| path tricks: double slash, `%61rchive`, encoded `?` | 403 | ✅ 403 (no bypass) |
| `GET /version`, `GET /_ping`, `GET /events` | 403 | ✅ 403 |
| Docker socket mounted in the test container; default client reaches a socket | no / no | ✅ not present; client fails |

The proxy's debug log shows each denied request with haproxy termination flag `PR--` (refused by the proxy, never forwarded to Docker).

Note: the Docker SDK's `events()` stream turns an HTTP error into `StopIteration` instead of raising. The test therefore checks the raw HTTP status for `/events` and for every endpoint the SDK cannot express.

### Known limitation found in A2.2: `GET /containers/{id}/attach/ws` returns 101

The WebSocket attach endpoint (`GET /containers/{id}/attach/ws`) is reachable through the proxy (HTTP `101 Switching Protocols`), a stdio hijack of a running container. The linuxserver proxy filters by **endpoint prefix and method only**: `attach/ws` is a `GET` under `/containers`, which architecture §2.10 explicitly allows ("GET /containers/*"), and it cannot be blocked without also blocking `GET /containers/{id}/json` (inspect), which the runner needs for its structural health check (A2.5). The non-WebSocket `POST /containers/{id}/attach` is already 403 (POST=0).

**Impact:** low. The containers' PID 1 (uvicorn for `cs-api`, the worker loop, `redis-server`) do not execute stdin, so no command runs; the capability is reading/writing the stdio of a container the runner can already inspect and stop. It is recorded, not fixed: blocking it would need a second filtering proxy, which is more attack surface than it removes. The test `test_container_stdio_hijack_is_a_known_limitation` documents the current behaviour (it fails if `attach/ws` ever stops returning 101, so a future proxy fix is noticed).

**Proposed for architecture §7.12 "Known limitations" (shared doc — for Usman/Tanzeel to add):**
> socket-proxy-rw filters by API endpoint and method, not by action, so a compromised runner can open `GET /containers/{id}/attach/ws` (stdio of a running container) in addition to stop/start/restart/kill and GET reads. No command executes, because the monitored containers do not read commands from stdin, and create/exec/delete remain impossible.

### Covered in CI (A2.2)

`runner.yml` now has a **`runner-proxy`** job that runs `run_proxy_test.sh` on every push and PR: GitHub-hosted runners have Docker and the host socket, so the proxy starts and the test container reaches it over `rw_proxy_net`. The script derives the API version from the host's `docker version` when `DOCKER_API_VERSION` is unset.

### Not covered here

- Through the **ro** proxy: any POST → 403 (second half of TEST-015, SEC-014). Added to `test_proxy.py` once `socket-proxy-ro` exists in `observability.yml` (Usman).
- TEST-015 is a **Gate G2** criterion (`phases.md`, G2 item 3): `docs/checks/gate-g2.md`.

## (a) socket-proxy-ro and Alloy — Usman

_To be filled in by Usman: Alloy `discovery.docker` + `loki.source.docker` through the ro proxy, the endpoints it calls (decides `NETWORKS=1`), and POST → 403._

## (b) nginx upstream `resolve` — Tanzeel — 2026-10-09 — **passed**

**Question (ADR-07, TB-011):** does open-source nginx ≥ 1.27.3 follow a Docker network alias with `server … resolve`, so that started and stopped replicas join and leave the upstream **without a reload**?

**Setup** (throwaway, not committed): image `nginx:1.30.5-alpine` (stable branch, nginx/1.30.5) on a user-defined bridge network; backends are `busybox:1.37` containers running `httpd` on port 8000, each answering with its own hostname, all with `--network-alias api-upstream`. Requests are sent from inside the nginx container (nothing published).

```nginx
events {}
http {
    resolver 127.0.0.11 valid=5s ipv6=off;
    upstream api {
        zone api 64k;
        server api-upstream:8000 resolve;
    }
    server {
        listen 80;
        location = /healthz { return 200 "ok\n"; }
        location / {
            proxy_pass http://api;
            proxy_next_upstream error timeout http_502;
        }
    }
}
```

**Results** (nginx master PID 1 throughout, restart count 0, so no reload or restart):

| Step | Result |
|---|---|
| nginx starts with **no** backend on the alias | Starts and stays up; `/healthz` → 200; `/` → 502 (`api-upstream could not be resolved`, then `no live upstreams`) |
| Start backend 1 | Served after 6 s; 12/12 requests to backend 1 |
| Start backend 2 | **Picked up after 3 s**; 12 requests split 6/6 |
| Stop backend 2 | **Dropped after 5 s**; 12/12 requests to backend 1, no client errors seen |
| Start backend 2 again | Back after 8 s |

**Conclusions for `cs-lb` (A1.2):**
- ADR-07 holds: `zone` + `resolver 127.0.0.11 valid=5s` + `server api-upstream:8000 resolve` follows scale-up and scale-down within TB-011's 10 s, with no reload.
- `cs-lb` can start before any api slot; it returns 502 until one is running. `ocp up` should still wait for the api slot to be healthy before declaring the stack healthy.
- Use `ipv6=off` (Docker's embedded DNS on these networks is IPv4) and `proxy_next_upstream error timeout http_502`, so a request that hits a replica being stopped is retried on another one.
- Pin `nginx:1.30.5-alpine` (or a later 1.30.x stable) for `cs-lb`.

## (c) `gemini-embedding-001` at 768 dimensions — Usman, pending

## (d) Supabase login and JWKS — Usman, pending
