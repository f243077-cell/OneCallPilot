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

### Result: 12/12 passed

| Request through socket-proxy-rw | Expected | Result |
|---|---|---|
| `GET /containers/json`, `GET /containers/{id}/json` | allowed | ✅ 200 |
| `POST /containers/{id}/stop`, `/start`, `/restart` | allowed | ✅ 204, state changed |
| `POST /containers/create` | 403 | ✅ 403 |
| `POST /containers/{id}/exec` | 403 | ✅ 403 |
| `DELETE /containers/{id}` | 403 | ✅ 403 |
| `POST /images/create` (pull) | 403 | ✅ 403 |
| `GET /images/json` | 403 | ✅ 403 |
| `POST /networks/create` | 403 | ✅ 403 |
| `POST /volumes/create` | 403 | ✅ 403 |
| `POST /containers/{id}/update` | 403 | ✅ 403 |
| `GET /version`, `GET /_ping` | 403 | ✅ 403 |
| `GET /events` | 403 | ✅ 403 |

The proxy's debug log shows each denied request with haproxy termination flag `PR--` (refused by the proxy, never forwarded to Docker).

Note: the Docker SDK's `events()` stream turns an HTTP error into `StopIteration` instead of raising. The test therefore checks the raw HTTP status for `/events`.

### Not covered here

- Through the **ro** proxy: any POST → 403 (second half of TEST-015, SEC-014). Added to `test_proxy.py` once `socket-proxy-ro` exists in `observability.yml` (Usman).
- `kill` is listed as allowed in architecture §2.10, but the runner does not use it, so it is not asserted.
- TEST-015 runs only through this script, not in CI. `runner.yml` CI runs unit tests from task A2.1.

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
