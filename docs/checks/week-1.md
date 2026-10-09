# Week 1 — checks and progress

> Evidence for Phase 0 and Week 1 (`phases.md` §5, §9, §11). The Friday checkpoint section is filled in on Friday.

## Phase 0 progress — Stream A (Tanzeel)

| Task | Status | Date | Evidence |
|---|---|---|---|
| S0.2 Repo skeleton (shared) | done (on `tanzeel`) | 2026-10-09 | Folders per `CLAUDE.md` §3, `.gitignore`, `.gitattributes`, CODEOWNERS (informational), PR template. Branch protection on `main`: Tanzeel, by hand, after the first PR exists. |
| S0.3 CI skeleton | done (on `tanzeel`) | 2026-10-09 | Six workflows with stable job names; `contracts.yml` runs real contract checks. |
| A0.2 Contracts C4, C6, C7 | drafted (on `tanzeel`) | 2026-10-09 | `contracts/actions.yaml`, `contracts/python/oncallpilot_contracts/ledger.py`, `contracts/telemetry.md`; contract tests green. Reader: Usman. |
| A0.3 Flutter project | done (on `tanzeel`) | 2026-10-09 | Package `com.tanzeel.oncallpilot`, minSdk 24, `FlutterFragmentActivity`, generated network security config (debug/profile only). `flutter analyze` and `flutter test` green. |
| A0.1 Firebase project | done | 2026-10-09 | Android app registered with package `com.tanzeel.oncallpilot`. `mobile/android/app/google-services.json` is present locally, git-ignored (`.gitignore:7`), not staged, not tracked, and in no commit; its `package_name` equals the `applicationId`. Not used by the app yet (no Firebase packages added). |
| S0.5 Network spike | **passed** | 2026-10-09 | Phone `192.168.0.105` reached `http://192.168.0.100:8000/healthz` over `SPARTACUS` (Private); `docs/checks/network.md` |
| S0.6 Docker Desktop | done on Tanzeel's laptop | 2026-10-09 | Below |
| S0.7 (a) socket-proxy-rw part | done | 2026-10-09 | `docs/checks/proxy.md` — TEST-015 rw part 12/12 against the real proxy |
| S0.7 (b) nginx upstream `resolve` | **passed** | 2026-10-09 | `docs/checks/proxy.md` — nginx 1.30.5 picks up a started replica in 3 s and drops a stopped one in 5 s, no reload |

## S0.6 — Docker Desktop (Tanzeel's laptop)

| Item | Value |
|---|---|
| Docker Desktop engine | 29.8.1, WSL2 backend (kernel 6.18.40.1-microsoft-standard-WSL2) |
| Docker API version | 1.56 (min 1.40) |
| Docker Compose | v5.5.1 (`include:` needs ≥ 2.20) |
| CPUs / memory visible to Docker | 4 / **9.72 GiB** after `.wslconfig` `memory=10GB` (was 7.69 GiB, the WSL default of half the host RAM) |
| Host RAM | 16 GB installed (15.9 GB usable reported by Windows) |
| `.wslconfig` | `%USERPROFILE%\.wslconfig` with `[wsl2]` `memory=10GB`; applied 2026-10-09 with `wsl --shutdown` and a Docker Desktop restart |
| Disk image | `%LOCALAPPDATA%\Docker\wsl\disk\docker_data.vhdx` on C: (15.3 GB; C: has 136 GB free, so it was not moved) |

**Resolved (2026-10-09):** the docs said 20 GB; the laptop has 16 GB. `CLAUDE.md`, `architecture.md` (v1.5) and `phases.md` now say 16 GB. Docker gets 10 GB, which meets §12.3 (at least 8 GB). Other projects' containers on this laptop (`bioguard-*`, restart policy on) must be stopped during benchmark runs (§12.3: no other heavy workloads).

## Phase 1 progress — Stream A (Tanzeel)

| Task | Status | Date | Evidence |
|---|---|---|---|
| A1.1 Chaos Shop services + C7 telemetry | done (on `tanzeel`) | 2026-10-09 | Below |
| A1.2 `cs-lb` and slots, releases 1.4.0/1.5.0/2.1.0/2.2.0 | done (on `tanzeel`) | 2026-10-09 | Below |
| A1.3 chaos CLI, seeded ledger, 8 faults | done (on `tanzeel`); scenario 5 waits for A1.4 | 2026-10-09 | Below |

### A1.1 — Chaos Shop baseline services

**Running set:** `cs-postgres` (postgres 17.11), `cs-redis` (redis 7.4.11, password, no persistence, no restart policy), `cs-payments` 1.0.0, `cs-api-140-1` 1.4.0 (alias `api-upstream`, `cpus: 0.5`), `cs-worker-210` 2.1.0 (alias `worker-upstream`, `restart: on-failure`), `cs-loadgen` (no `oncallpilot.*` labels). All on `chaos_net`; nothing published. Start: `docker compose --profile testbed up -d --wait` from the repository root (needs the git-ignored root `.env`, see `.env.example`).

**Checks (2026-10-09):**
- Unit and contract tests (`chaos-shop/`, `uv run pytest`): 53 passed, including the C7 metric contract for api and worker (TB-003) and 1,000+ api log lines against the C7 log schema (TB-004). ruff and `mypy --strict` clean.
- Live checks against the running containers (`uv run pytest -m live`): 8 passed. All six containers healthy; labels exactly as C7 §1; no published ports; `/metrics` of api and worker conform (including `process_*` with base labels); the last 1,000 log lines of api, worker and payments conform; **baseline traffic 5.04 rps** from the loadgen (5.14 rps including health checks), within TB-005's 5 ± 1.
- A worker started with an invalid `QUEUE_BATCH_SIZE` exits 1 with one CRITICAL JSON line carrying the stack trace.

**Memory (`docker stats`, baseline, 2026-10-09):**

| Container | Used | Limit |
|---|---|---|
| cs-api-140-1 | 49 MiB | 256 MiB |
| cs-worker-210 | 45 MiB | 256 MiB |
| cs-postgres | 43 MiB | 256 MiB |
| cs-loadgen | 39 MiB | 128 MiB |
| cs-payments | 37 MiB | 128 MiB |
| cs-redis | 4 MiB | 64 MiB |
| **Total** | **≈ 215 MiB** | 1,088 MiB |

Within the §12.4 estimate (Chaos Shop baseline ≈ 0.6 GB); A1.2 adds `cs-lb` and the stopped slots, which use no memory while stopped.

**Notes for later tasks:**
- Fault behaviour (worker memory growth, slow checkout queries, the 1.5.0 checkout change, the coupon parser, the 2.2.0 config) is A1.3. The loadgen spike rate (`CS_LOADGEN_SPIKE_RPS`, placeholder 40) is calibrated in A1.4.
- `cs-loadgen` sent to `api-upstream:8000` directly until A1.2 added `cs-lb`; it now sends to `http://cs-lb`.
- The two C7 deviations are recorded under "C7 deviations" below.

### A1.2 — `cs-lb` and slots

**What exists now:**
- `cs-lb` (`nginx:1.30.5-alpine`, `chaos-shop/lb/nginx.conf`): `zone api 64k`, `server api-upstream:8000 resolve`, `resolver 127.0.0.11 valid=5s ipv6=off` (the S0.7(b) settings), C7 JSON access log on stdout, plain-text error log on stderr, `/healthz` answered by nginx itself, `/internal/*` proxied to the api but not access-logged. Published on `127.0.0.1:8080` only (architecture §3). Read-only root filesystem.
- All 12 slots (TB-002): `cs-api-140-1..5`, `cs-api-150-1..5`, `cs-worker-210`, `cs-worker-220`, each with the five C7 labels (replica from the name) and the alias `api-upstream` or `worker-upstream`.
  - Baseline slots `cs-api-140-1` and `cs-worker-210` are in profile `testbed`.
  - The other 10 are in profile `testbed-slots`: created with `docker compose --profile testbed-slots create`, never started by Compose, so `up` never starts them and a repeated `up` leaves them stopped.
- `releases.yaml`: api 1.4.0 and 1.5.0, worker 2.1.0 and 2.2.0, each with a full 40-character commit SHA (simulated history, so not commits of this repository) and a neutral commit message. Images `chaos-shop/api:1.4.0`, `:1.5.0`, `chaos-shop/worker:2.1.0`, `:2.2.0` all build. In A1.2 the 1.5.0 and 2.2.0 images differ from the baseline only in release and commit; their behaviour changes come in A1.3.
- `cs-loadgen` now sends through `http://cs-lb`.

**Two changes from the spike settings, both found by the TB-011 live test:**
1. `max_fails=1 fail_timeout=2s` on the upstream server. A started slot is in DNS about 4–8 s before its app listens. A request that nginx sends there early is refused and retried on another slot, and nginx's default `fail_timeout=10s` then skipped the new slot for 10 s. With 2 s, nginx serves a slot 0.3–1.9 s after its app is ready.
2. `proxy_next_upstream error timeout` without `http_502`: cs-api returns 502 itself for a failed payment, and that must reach the client unchanged.

**Checks (2026-10-09):**
- Unit tests: 62 passed. The new ones check the slot inventory, labels from the name, aliases, profiles, no ports except `cs-lb`, a release and a build for every image, and the nginx settings and C7 log fields. ruff and `mypy --strict` report no errors. `nginx -t` passes.
- Live checks: 11 passed.
  - All slots exist, stopped, with labels and aliases (TB-002).
  - The `cs-lb` log lines conform to C7.
  - Baseline traffic through `cs-lb` is 4.84–5.04 rps.
- **TB-011 scale 1 → 3 → 1**, measured on the containers' own clocks, 5 runs:
  - **Scale-up:** `cs-lb` served each new slot 0.3–3.0 s after its app was ready, and 30 requests split about 10/10/10 across the three slots. Before that, app start-up took 6.1–8.9 s at `cpus: 0.5` with two slots starting at once (one earlier run took 11 s while the host had only 1.2 GB free).
  - **Scale-down:** after the 10 s window, 20/20 requests went to `cs-api-140-1` and nginx logged no connect errors. Inside the window, 0–1 of about 900 requests per run got a 504. nginx does not retry a connect timeout to the IP of a just-stopped slot.
- **Rerun from container start (2026-10-09, `bioguard-*` stopped, no other containers running), 5 runs, 10 slot starts:**

  | Run | Slot | App ready after start | Served by `cs-lb` after ready | **Start → served** |
  |---|---|---|---|---|
  | 1 | 140-2 / 140-3 | 5.4 / 5.7 s | 2.4 / 2.4 s | 7.8 / 8.1 s |
  | 2 | 140-2 / 140-3 | 5.7 / 6.0 s | 1.5 / 1.4 s | 7.2 / 7.4 s |
  | 3 | 140-2 / 140-3 | 6.6 / 7.0 s | 0.8 / 3.3 s | 7.4 / **10.3 s** |
  | 4 | 140-2 / 140-3 | 7.0 / 6.6 s | 1.0 / 1.1 s | 8.0 / 7.7 s |
  | 5 | 140-2 / 140-3 | 7.5 / 8.2 s | 1.0 / 3.0 s | 8.5 / **11.2 s** |

  TB-011 (accepted ruling: measured from app-ready) passes in every run: 0.8–3.3 s. **Flag:** measured from container start, 2 of 10 starts took longer than 10 s (10.3 and 11.2 s). Windows reported only 0.5–0.9 GB free of 15.9 GB during these runs (Docker's WSL VM holds up to 10 GB; desktop apps use the rest). App start-up at `cpus: 0.5` is the variable part. CPU limits and architecture §12.4 are unchanged, as ruled.

**Memory (`docker stats`, baseline, 2026-10-09):** running containers use about **222 MiB**; their limits total **1,088 MiB**, the same as A1.1, because `cs-payments` went from 128 MiB to 96 MiB to make room for `cs-lb` (32 MiB, uses 3 MiB). Stopped slots use no memory. Each extra running api slot adds about 50 MiB (limit 256 MiB), so scale-to-3 adds about 100 MiB. Docker has 9.72 GiB; the Windows host showed 1.2 GB free of 15.9 GB during the test, and other projects' containers (`bioguard-*`, about 145 MiB) were running again.

### A1.3 — chaos CLI, seeded ledger, fault mechanics

**How to run** (repository root, testbed up): `uv run --project chaos-shop/cli chaos reset | status [--json] | inject <1-8 or name> | verify <1-8 or name> [--hold 180] [--recover 120]`.

**What exists now:**
- `chaos-shop/cli/`: the `chaos` CLI, its own uv project (Docker SDK, pyyaml, `oncallpilot-contracts` as a path dependency). It reads `CHAOS_TOKEN` from the environment or the git-ignored root `.env` and never prints it.
- **Networking (approved option C):** every command batches its calls into one short-lived helper container, `python:3.12.15-slim` on `chaos_net`, named `ocp-ops-<hex>`. The helper gets the token as an env var, addresses slots by container name, runs `chaos_cli/helper.py` (standard library only) and is removed afterwards. Nothing is published and nothing execs into a monitored container. The helper also mounts the ledger volume and is the only writer of the CLI's ledger lines.
- **Ledger (C6, TB-009):** `chaos reset` atomically replaces `deploys.jsonl` with the seeded history from `releases.yaml` (5 deploys of 1.2.0, 2.0.0, 1.3.0, 2.1.0, 1.4.0), followed by one `kind=reset` record per service. `inject 2` and `inject 6` append `kind=deploy` with `deployed_by=ci`; `verify`'s direct fixes append `rollback` or `scale` with `deployed_by=setup`. Every line is built and validated with `DeployRecord`. `config_hash` = SHA-256 of the release's `config` in `releases.yaml`, and a test checks that each slot runs exactly that configuration.
- **Releases behave differently now:**
  - **1.5.0:** checkout rounds totals with a step table that is off by one, so orders of 50.00 or more raise `IndexError` and return 500.
  - **2.2.0:** the slot runs `QUEUE_BATCH_SIZE=250`, above the worker's limit of 100. The worker logs one CRITICAL line with a stack trace and exits 1, and `restart: on-failure` keeps restarting it.
- **Fault switches** are `/internal/chaos/*` on cs-api and cs-worker, `/internal/delay` on cs-payments and `/internal/mode` and `/internal/coupon-stream` on cs-loadgen. All need `CHAOS_TOKEN` and none are logged.
- **Faults survive restarts (ADR-19):**
  - The worker's retention marker lives in the container's writable layer: an OOM kill (SIGKILL) keeps it and a graceful stop removes it.
  - The payments delay and the loadgen mode and coupon stream sit in state files under `/var/lib/shop`, so they survive `docker restart` (checked live).
  - Scenario 3's switch is in process memory on purpose, because a restart is its fix.
  - `chaos reset` clears everything: graceful restart of the baseline slots, explicit switch-off calls, and a reseeded ledger.
- **Scenario 8:** the loadgen sends 2 checkouts per second with crafted coupon codes. They carry the planted texts from architecture §11.3. The parser in both releases raises `ValueError` whose message repeats the code, so the text lands in `msg` and `exc_message`. They are test data for TEST-003.
- **Worker slots** now have `memswap_limit: 256m` (no swap). With swap, the leaking worker paged out and was OOM-killed only once in 15 minutes. Without swap it is killed and restarted about every 210 s, with retention still on afterwards (observed 2 restarts in 8 minutes).
- **Health checks** use `start_interval: 2s`, so slots report healthy soon after their app listens.

**Checks (2026-10-09):**
- Unit tests: chaos-shop 75 passed; CLI 30 passed. The CLI tests cover ledger validity (TB-009), the TB-010 grep (releases.yaml, runbooks/, names, labels and aliases, and a sample ledger written by the CLI), reset, status, every inject and fix against a fake Docker, the token, and the helper's ledger writes. ruff and `mypy --strict` report no errors in both projects.
- Live, service checks (`chaos-shop`, `pytest -m live`): 11 passed.
- Live, CLI (`chaos-shop/cli`, `pytest -m live -k "not verify"`): 10 passed. Reset is within 90 s and `status` then shows the baseline (TB-002, TB-007). Each of the 8 injects finishes within 30 s and shows up in `status` (TB-006). The live ledger is valid and neutral after injecting 2 and 6 (TB-009, TB-010).
- **`chaos verify` with the real 180 s hold and 120 s recovery limit (TB-008):**

  | # | Scenario | Inject | Broken after 180 s | Fix | Recovered after fix |
  |---|---|---|---|---|---|
  | 1 | memory-leak | 2.1 s | rss 234 MiB, retention on | graceful restart of the worker | 18 s (rss 60 MiB) |
  | 2 | bad-deploy | 13.7 s | 10/10 checkouts 500 | 140 slots started, 150 stopped, ledger `rollback` | 3 s (10/10 201) |
  | 3 | db-pool | 2.3 s | 13 pool timeouts in 10 s, pool 5/5 in use | restart of the api slot | 12 s (0 timeouts) |
  | 4 | cache-outage | 0.3 s | cs-redis exited, checkouts 503 | start cs-redis | 9 s |
  | 5 | traffic-spike | 1.4 s | **not broken**: p95 8 ms at 40 rps | — | — |
  | 6 | config-crash | 2.8 s | cs-worker-220 restarting, 11 restarts | 210 started, 220 stopped, ledger `rollback` | 10 s |
  | 7 | slow-dependency | 1.4 s | checkouts 201 in 2.55–2.62 s | none (escalate) | — |
  | 8 | log-injection | 1.4 s | 20 checkout 500s in 10 s | none (escalate) | — |

  Reset takes 10–21 s per run. **Scenario 5 fails until A1.4:** the spike rate is still the placeholder (40 rps, which the loadgen reaches), and one api slot handles it with p95 8 ms. A1.4 calibrates `CS_LOADGEN_SPIKE_RPS`.
- **15-minute persistence** (`verify 1 --hold 900`): still broken after 900 s and fixed 17 s after a graceful restart. The other 7 scenarios still need their 15-minute run before Gate G1.

**Memory (baseline after reset, 2026-10-09):** about **227 MiB** used, limits unchanged at **1,088 MiB**. During scenario 1 the worker climbs to its 256 MiB limit and is killed. The helper container (limit 64 MiB) lives about 1–2 s per command. The Windows host had 1.7 GB free of 15.9 GB, with no other containers running.

## C7 deviations (accepted for now, 2026-10-09)

Both are in the implementation; `contracts/telemetry.md` is unchanged. Usman decides whether the contract is tightened or amended in a separate `contract/*` PR.

1. **No access-log line for `/metrics` and `/internal/*`** in cs-api (and cs-payments, and `/internal/*` in cs-lb). C7 §3.2 says the api writes one access line per request, but these paths have no C7 route value, and admin calls should not appear in agent-visible logs.
2. **No `route` field on cs-payments request lines.** C7 route values name cs-api routes only. cs-payments lines carry `request_id`, `status` and `duration_ms`. (cs-lb lines have no `route` either, which C7 §3.3 already specifies.)

## `chaos_net` for other Compose files

`chaos_net` is declared in `infrastructure/compose/testbed.yml`; the network is `oncallpilot_chaos_net`. Tested on 2026-10-09 with a scratch project that includes `testbed.yml` plus a second file, as `compose.yaml` does:
- Second file declares `chaos_net` with `external: true`: Compose merges it as external, so nobody creates it, and a fresh `up` fails with `network ... declared as external, but could not be found`.
- Second file uses `chaos_net` and declares it plainly (`chaos_net: {}`), or not at all: one shared network, created on the first `up`. **This is the form that works** for files included by `compose.yaml`. `external: true` only fits a compose project started separately, after the testbed.

## Compose profiles

Architecture §12.1 lists four profiles (`testbed`, `obs`, `copilot`, `runner`). The testbed now has a fifth, **`testbed-slots`** (accepted 2026-10-09): the ten non-baseline slots, created but never started by Compose. **`ocp up` (S1.1) must create them**: run `docker compose --profile testbed-slots create` after `up`, or rely on `chaos reset`, which `ocp up` runs and which creates any missing slot itself.

## Deploy ledger location (agreed 2026-10-09)

- Docker volume **`deploy_ledger`** (`oncallpilot_deploy_ledger`). The chaos CLI's helper container creates it on the first `chaos reset`; `runner.yml` (rw, task A2.1) and Usman's `copilot.yml` (ro) declare `deploy_ledger: {}` and mount it. Compose adopts a volume that already exists (tested 2026-10-09). A declared volume that no service mounts is not created by `up`.
- **`LEDGER_PATH=/ledger/deploys.jsonl`** in every container that mounts it.
- The file and the directory belong to **uid 10001** (mode 0644), so the runner must run as uid 10001, the same non-root user as the Chaos Shop images.

## Friday checkpoint (W1)

_To be filled in: stack boots with one command; phone reaches the laptop._
