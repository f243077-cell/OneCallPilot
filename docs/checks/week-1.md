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
| A1.3 chaos CLI, seeded ledger, 8 faults | done (on `tanzeel`); scenario 5 completed in A1.4 | 2026-10-09 | Below |
| A1.4 traffic spike calibrated: 1 replica saturates, 3 recover | done (on `tanzeel`) | 2026-10-09 | Below |
| A1.5 Flutter skeleton: env config, go_router, theme, login, secure session, `MockIncidentRepository` | done (on `tanzeel`); device checks open | 2026-10-10 | Below |
| A1.6 Incident Feed and Incident Detail (hypotheses, `E#` chips, evidence cards with fl_chart) in mock mode | done (on `tanzeel`); checked on the phone | 2026-10-10 | Below |

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

  Reset takes 10–21 s per run. **Scenario 5 fails until A1.4:** the spike rate is still the placeholder (40 rps, which the loadgen reaches), and one api slot handles it with p95 8 ms. A1.4 calibrates `CS_LOADGEN_SPIKE_RPS`. (Done: see A1.4. The old check, a `GET /products` probe, could not have seen the spike either; A1.4 replaced it.)
- **15-minute persistence** (`verify 1 --hold 900`): still broken after 900 s and fixed 17 s after a graceful restart. All 8 then passed the 15-minute run on 2026-10-09; see the overnight run below.

**Memory (baseline after reset, 2026-10-09):** about **227 MiB** used, limits unchanged at **1,088 MiB**. During scenario 1 the worker climbs to its 256 MiB limit and is killed. The helper container (limit 64 MiB) lives about 1–2 s per command. The Windows host had 1.7 GB free of 15.9 GB, with no other containers running.

**A1.3 deviations (accepted 2026-10-09):**
1. Scenario 8's crafted checkouts come from `cs-loadgen` (switched on by `chaos inject 8`), not from the CLI itself, because `inject` must return within 30 s while the traffic continues. **One-line correction** to architecture §11.3, row 8: "Chaos CLI sends 2 rps of checkout requests…" → "The chaos CLI switches on a `cs-loadgen` stream of 2 rps of checkout requests…". Approved and applied in its own docs commit `84b4779` (2026-10-09).
2. Command times: `inject` is at most 13.7 s (TB-006, 30 s); `reset` takes 10–21 s (TB-007, 90 s); `verify` takes about 4 minutes, because its 3-minute hold comes from the spec.
3. Worker slots run with `memswap_limit: 256m`, and the health checks use `start_interval: 2s`.
4. The TB-010 grep leaves out `deploy_id` (a random UUID) and `image_tag` (fixed by C6, not agent-visible).
5. Scenario 3's switch lives in process memory on purpose: a restart is its fix.

**Logger names (C7 change, 2026-10-09):** `logger` is agent-visible, so Chaos Shop code now logs as `shop.<area>` (`shop.access`, `shop.checkout`, `shop.jobs`, …) instead of `chaosshop.*`; nginx keeps `nginx.access`. Contract commit `2910d52` changes only `contracts/telemetry.md`; the code and the C7 checks (`chaos-shop/tests/contract.py` now rejects a non-neutral `logger`; the CLI's TB-010 grep checks every logger name in the services and `nginx.conf`) are a separate commit. `contracts/VERSION` stays 0.1.0 while the contracts are unreleased drafts (no `contracts-v0.1.0` tag yet).

**Overnight persistence run (TB-008, before Gate G1, after A1.4):** `uv run --project chaos-shop/cli chaos verify --all --hold 900 > verify-overnight.log` runs all 8 scenarios in a row (about 2.5 hours), prints progress as it goes and ends with a PASS/FAIL table. Run it with nothing else on the host.

**Overnight persistence run, result (2026-10-09, 16:08–18:15):** `chaos verify --all --hold 900`, run unattended with only the baseline testbed on the host: **8/8 PASS in 127 minutes** (TB-008: each fault persists for 15 minutes; 1–6 are fixed by their expected action within the 120 s limit). The log is `verify-overnight.log` in the repository root; it is not committed.

| # | Scenario | Still broken after 900 s | Recovered after fix |
|---|---|---|---|
| 1 | memory-leak | retention on, 4 OOM restarts during the hold (rss 81 MiB just after a restart) | 15 s |
| 2 | bad-deploy | 10/10 checkouts 500 | 2 s |
| 3 | db-pool | 7 pool timeouts in 10 s, pool 5/5, `/orders` 503s | 12 s |
| 4 | cache-outage | cs-redis exited, checkouts 503 | 8 s |
| 5 | traffic-spike | 1 slot: 107 rps, p95 4.5 s, 5xx 0.1 % | 43 s (3 slots: 103 rps, p95 99 ms, 5xx 0 %) |
| 6 | config-crash | cs-worker-220 restarting, 21 restarts | 10 s |
| 7 | slow-dependency | checkouts 201 in 2.55–2.60 s | persists (escalate) |
| 8 | log-injection | 20 checkout 500s in 10 s | persists (escalate) |

Injects took 0.3–9.7 s and resets 9.9–16.0 s. After the run, `chaos status` shows the baseline; the running containers use about 254 MiB, and Windows had 2.85 GB free.

Two observations, both accepted as they are (ruling of 2026-10-10):
- **Scenario 5 after 15 minutes** was broken by latency (p95 4.5 s, 4.5× the check's 1 s floor), but its 5xx share had fallen to 0.1 % (4–8 % in the first minutes). The detector's p95 rule (≥ 300 ms) still fires; its error-rate rule (≥ 2 %) may not at that point. This is expected: the loadgen sheds requests at its in-flight cap under saturation, so fewer requests reach the api to fail. **Note for the detector (Usman):** scenario 5 must be detected by the `p95_latency` rule; it must not depend on the `error_rate` rule, whose 5xx share drops below 2 % within 15 minutes.
- **Scenario 1** is checked between OOM kills, so rss can be low at the moment of the check. The check counts restarts (`restart_count` is the signal), which is what keeps it broken.

### A1.4 — traffic spike calibration (TB-005, scenario 5)

**Result:** `CS_LOADGEN_SPIKE_RPS` is **100** (was the 40 rps placeholder), with a 30 s ramp; the baseline stays at 5 rps. CPU and memory limits are unchanged (`cs-api-*` `cpus: 0.5`, `cs-loadgen` `cpus: 1.0`).

**How it was calibrated** (2026-10-09, integration host: Docker with 4 CPUs and 9.7 GiB, Windows 1.0–1.8 GB free, no other containers):
- A scratch sampler container on `chaos_net` (not committed) read every api slot's `/metrics` every 13 s. It computed what the detector's PromQL computes (architecture §6.8): request rate, 5xx share and histogram p95 over all routes except `/healthz`. `docker stats` ran alongside.
- **Baseline:** 5.0–5.3 rps, p95 48–121 ms, no 5xx.
- **Ramp 5 → 250 rps over 10 minutes, one slot:**
  - Up to about 40 rps, p95 stays at 70–95 ms.
  - From about 45 rps the slot's CPU sits at its 0.5 limit, and p95 swings between 0.4 and 9 s.
  - The slot never served more than 75–98 rps.
  - Near 100 rps, `cs-loadgen` reached its own 1.0 CPU limit.
- **Steady 60 rps, one slot, 3 minutes:**
  - It served all 60 rps with no 5xx; p95 was 0.2–1.5 s.
  - This is degraded but keeps up, so it is not a saturation: **rejected**.
- **Steady 100 rps, one slot, two 3-minute holds:**
  - The loadgen sends 84–99 rps, and the slot serves 76–92 rps.
  - p95 is 8.2–9.6 s, and checkout p95 is 10–25 s.
  - 3.5–12 % of requests get 503 (`DatabasePoolTimeout`).
  - The loadgen sheds requests at its 256 in-flight cap.
  - All of this held for the whole hold.
- **After scaling to 3 slots at 100 rps:**
  - Each slot uses 10–50 % of its 0.5 CPU.
  - p95 is 95–130 ms with 0 % 5xx, serving 94–103 rps.
- **Why 100 rps:**
  - It is at least 10 % above the best throughput one slot reached, so a single slot can never catch up and the queue keeps growing.
  - It is about 40 % of the capacity of three slots.
  - It cannot go higher: `cs-loadgen` is one Python process and produces at most about 100 rps at `cpus: 1.0`.

**What the agent will see in scenario 5:**
- The api request rate goes from 5 to about 90 rps, and api CPU sits at its limit.
- p95 is about 9 s, and 4–8 % of requests get 503 `DatabasePoolTimeout`.
- The pool timeouts are a side effect: CPU-starved handlers hold their connections longer.
- Scenario 3 also shows pool timeouts, but there the request rate stays at 5 rps and CPU stays low. The `request_rate` and `cpu_usage` templates tell the two apart.

**`chaos verify 5` check changed:**
- The A1.3 check probed `GET /products` through `cs-lb`. `/products` is served from the cache, so it stayed at 15–300 ms while checkout waited 10–25 s; it could not see the spike.
- The check now reads every running api slot's C7 counters over 15 s, the same way as the detector:
  - **Broken:** p95 ≥ 1 s or 5xx ≥ 5 %, with the loadgen in spike mode. Both are well past the detector's 300 ms and 2 % floors.
  - **Recovered:** at least 3 slots, p95 < 300 ms and 5xx < 2 %, while still serving at least 70 % of the spike rate. The last condition shows the load is still there and the slots carry it.

**Recovery against the limits:**
- `chaos verify` requires recovery within 120 s of the fix (architecture §11.3). Scenario 5 recovered in 44–46 s, measured from the scale command. That time includes the slots starting (about 8 s) and the 15 s check window.
- The sampler showed p95 back at 95 ms within 26 s of the scale.
- The worker's signal verification (normal for 60 s within 300 s, architecture §5.10) therefore has room.
- After `chaos reset`, requests already queued drain for about 25 s. The detector's 180 s warm-up covers this, and the live TB-005 check waits 30 s.

**Memory and CPU:**
- With 3 api slots at the spike, running Chaos Shop containers use about **330 MiB** (two extra slots at 27–50 MiB each). The limits of the running containers go from 1,088 to 1,600 MiB.
- Peak CPU across the testbed is about 2.9 of 4 cores, mostly the loadgen (up to 100 %) and the three api slots.
- Windows had 1.55–1.8 GB free during the runs; Docker Desktop stayed up.

**Checks (2026-10-09):**
- **Unit tests:** chaos-shop 77 passed and CLI 37 passed; ruff and `mypy --strict` are clean.
  - New in chaos-shop: TB-005 driven by the compose values: 5 ± 1 rps over each minute at baseline, and 100 rps held after the 30 s ramp.
  - New in the CLI: Prometheus-style `histogram_quantile`; the request window (slots summed, `/healthz` excluded, restarted slots counted from zero); the scenario 5 checks (one slow slot is broken; recovery needs 3 slots, low p95 and the spike load still served).
- **Live TB-005** (`chaos-shop/cli`, `pytest -m live -k baseline_traffic`): passed. After reset the api serves 5 ± 1 rps over 60 s, measured from its own counters as Prometheus would, with no 5xx.
- **`chaos verify --all --hold 180`**, 2026-10-09, one unattended run of 31 minutes: **8/8 PASS**.

  | # | Scenario | Inject | Broken after 180 s | Recovered after fix |
  |---|---|---|---|---|
  | 1 | memory-leak | 1.7 s | rss 230 MiB, retention on | 15 s |
  | 2 | bad-deploy | 9.9 s | 10/10 checkouts 500 | 3 s |
  | 3 | db-pool | 1.6 s | 25 pool timeouts in 10 s, pool 5/5 | 13 s |
  | 4 | cache-outage | 0.3 s | cs-redis exited, checkouts 503 | 8 s |
  | 5 | traffic-spike | 1.6 s | 1 slot: 101 rps, 5xx 5.7 %, p95 8.6 s | **44 s** (3 slots: 103 rps, 5xx 0 %, p95 107 ms) |
  | 6 | config-crash | 3.2 s | cs-worker-220 restarting, 11 restarts | 10 s |
  | 7 | slow-dependency | 1.5 s | checkouts 201 in 2.55–2.59 s | persists (escalate) |
  | 8 | log-injection | 1.9 s | 20 checkout 500s in 10 s | persists (escalate) |

  Each reset took 10.6–17.2 s.

**Deviations:** none from the architecture. Two things to know:
1. The loadgen runs at its CPU ceiling during the spike. If the host is busier (for example the full stack in W2), the loadgen sends a little less than 100 rps. One slot still saturates above about 75–90 rps, and the check needs only 70 % of the spike rate served. The overnight `verify --all --hold 900` with the full stack will show whether there is enough margin.
2. Errors during the spike are pool timeouts, as described above.

### A1.5 — Flutter skeleton (MOB-001, MOB-002, MOB-017)

**What exists now** (`mobile/`, layout of architecture §10.1):
- **Packages** (approved 2026-10-10): flutter_riverpod 3.4.3, go_router 18.0.2, supabase_flutter 2.18.2, flutter_secure_storage 11.2.0, json_annotation 4.12.0. Dev only: json_serializable 6.14.1, build_runner 2.15.1. They are locked in `pubspec.lock`.
- **Config** (`core/config/env.dart`): `DATA_SOURCE` (`mock`, the default, or `live`), `API_BASE_URL`, `WS_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY` from `--dart-define`. A live build that lacks one shows which instead of starting.
- **Routes** (`core/router/app_router.dart`): every route of §10.2.
  - Signed out, every route except `/login` goes to `/login`.
  - A deep link such as a push tap's `/incidents/:id` is kept in `?from=` and opened after sign-in. Only in-app paths are accepted there.
  - Screens that later tasks build (detail A1.6, review A2.7, execution A2.9, audit A3.5) are placeholders.
  - The feed lists the repository's incidents until A1.6 builds the real one.
- **Login and session** (MOB-001):
  - `AuthRepository` has two implementations. `SupabaseAuthRepository` signs in with email and password. `MockAuthRepository`, used in mock mode, accepts any email with a password of at least 6 characters.
  - Supabase gets `SecureSessionStorage` and `SecureAsyncStorage`. Both write through `flutter_secure_storage`, so the session and PKCE data never go to shared preferences.
  - Sign-out clears the stored session. Deleting the device registration joins sign-out in A3.2 (push).
- **Data layer behind an interface:**
  - Screens and providers use only `IncidentRepository`: list, detail, events, challenge, approve and reject.
  - `MockIncidentRepository` implements it now; `LiveIncidentRepository` (A3.1) will implement the same interface, so the screens do not change.
- **DTOs** (`data/models`, json_serializable, generated `*.g.dart` committed): C1 `IncidentSummary`/`IncidentDetail`, `Evidence`, `Hypothesis`, `Proposal` with its dry run, `Execution`; C3 `WsEvent`; C8 challenge, approve and reject bodies.
  - Every enum has an `unknown` case for values this build does not know (MOB-020).
  - Two parts stay raw maps until the screens that show them: evidence payloads (A1.6) and `health_after` (A2.9).
- **Mock mode** (MOB-017, `MockIncidentRepository`):
  - It replays every timeline with its delays and moves all timestamps so the incident opens "now"; expiry countdowns are therefore real.
  - It builds the incident from the events emitted so far, and returns the timeline's `final` at the end.
  - At an `await_approval` step it waits for the user's approval; a timeline may wait twice (primary, then rollback).
  - Challenge, approve and reject run the protocol's checks in its order:
    - `NOT_FOUND`;
    - `IDEMPOTENCY_KEY_REQUIRED`, and replay or `IDEMPOTENCY_CONFLICT` for a reused key;
    - single-use `CHALLENGE_INVALID`;
    - `STALE_PROPOSAL`, `PROPOSAL_NOT_PENDING`, `PROPOSAL_EXPIRED`;
    - `BIOMETRIC_REQUIRED` for medium and high tiers;
    - `VALIDATION_ERROR` for a reject reason.
  - Codes that need server state (`RATE_LIMITED`, `COOLDOWN_ACTIVE` with `retry_after`, `UNAUTHORIZED`, `INTERNAL`, …) are simulated with `failNext`.
  - A rejection escalates the incident with `proposal_rejected` and stops the replay.
- **Fixtures at run time:**
  - `dart run tool/sync_fixtures.dart [--from <contracts dir>]` copies `contracts/fixtures/timelines/*.json` into `mobile/assets/fixtures/timelines/`. The copies are git-ignored (`mobile/assets/fixtures/**/*.json`); only the script and a `.gitkeep` are committed.
  - Until C1 is on `main`, run it against a worktree of `origin/usman`.
  - Without synced fixtures, the app starts with a message naming the command.
- **CI** (`.github/workflows/mobile.yml`, replacing the skeleton step): locked `pub get`, the DTO code regenerates with no diff, `flutter analyze`, `flutter test`.
  - `subosito/flutter-action` is pinned to commit `1a449444c387b1966244ae4d4f8c696479add0b2` (v2.23.0, which is also the `v2` tag); checked 2026-10-10 with `git ls-remote` and the GitHub releases API.
  - `actions/checkout@v4` is GitHub's own action, so it stays on its tag; it is the only other action in our workflows.

**Checks (2026-10-10):**
- `flutter analyze`: no issues.
- `flutter test` on `tanzeel`: **25 passed**. The fixture groups are skipped because the C1/C3 fixtures are not on this branch.
  - Router: the redirect rules, a deep link opened after sign-in, a refused sign-in, sign-out back to login (MOB-002).
  - Session storage: only in the secure store, kept across a restart, cleared by sign-out (MOB-001).
  - Mock repository: 9 tests covering every check and code above (MOB-017).
  - Env, app start, startup error.
- `OCP_CONTRACTS_DIR=<worktree of origin/usman>/contracts flutter test`: **55 passed**.
  - The real DTOs parse all 8 C3 event fixtures.
  - All 3 timelines replay through `MockIncidentRepository` to their `final` state. `scale_failed_rollback` needs two approvals.
- `flutter build apk --debug --dart-define=DATA_SOURCE=mock`: builds in 2.5 minutes.
- Regenerating the DTO code gives identical files.

**Not checked yet (needs the phone or the emulator, or Usman's B0.1):**
- MOB-001's acceptance (stay signed in across an app restart, session not in shared preferences) on a device against the real Supabase project.
- MOB-017's full flow (feed → detail → review → approve → execution → resolved) needs the screens of A1.6, A2.7 and A2.9. A1.5 provides the replay and approval engine they will use.
- MOB-002's manual push-tap test belongs to A3.2.

**Laptop-specific workaround, accepted 2026-10-10** (`mobile/android/gradle.properties`). It is needed on Tanzeel's integration laptop; another machine may not need it:
1. The Gradle heap is 2 GB instead of the template's 8 GB: the laptop has 16 GB, and Docker holds up to 10 GB.
2. `kotlin.incremental=false`: the first build failed in `url_launcher_android` with "Could not close incremental caches", because the plugin sources sit in the pub cache on C: and the build on D:.

### A1.6 — Incident Feed and Incident Detail (MOB-003, MOB-004, MOB-005)

**What exists now** (mock mode, through `IncidentRepository`; nothing in `contracts/` changed):
- **Package** (approved 2026-10-10): fl_chart 1.2.0, which brings equatable 2.1.0 (pure Dart).
- **Payload DTOs** (`data/models/evidence_payloads.dart`): one json_serializable class per C1 evidence kind (`DetectorSignalPayload`, `LogQueryPayload`, `MetricQueryPayload`, `DeployListPayload`, `ServiceHealthPayload`, `RunbookHitPayload`), plus the enums `LogLevel`, `MetricTemplate`, `DeployKind` and `DeployedBy`. An unknown kind, or a payload of the wrong shape, parses to null instead of throwing.
- **Feed** (`/incidents`, MOB-003):
  - Open and past tabs. Each row shows a severity badge, the title, service · status (with the escalation reason or the resolution), and a "time since alert" that updates every 5 s.
  - `incident.opened` is applied from the event itself. Other events refetch the list; events with an old `state_version` are ignored (§10.3).
  - Pull to refresh. States for loading, error with Retry (showing the ApiError's message), and an empty tab.
- **Detail** (`/incidents/:id`, MOB-004):
  - Header with severity, service, status and age, and a "Review proposed action" button when a proposal is pending (it opens `/proposals/:id`, task A2.7).
  - Hypotheses ranked by `rank`, each with category, confidence bar and percentage, and `E#` chips. A chip scrolls to its evidence card; a chip for an unknown ref is disabled.
  - Dropped hypotheses are hidden behind "Show N dropped".
  - It refetches on a newer event for this incident. States for loading, not found, error with Retry, and no hypotheses or evidence yet.
- **Evidence cards** (`shared/widgets/evidence_card.dart`, MOB-005):
  - **Alert signal:** value against baseline, and z.
  - **Log:** the lines, with ERROR and CRITICAL highlighted; "n of total"; top exception types; or "No matching lines".
  - **Metric:** an fl_chart line, with the incident's span shaded (from the anomaly start to the incident end or the last point) and the change point as a dashed line. Baseline, peak and % change appear below. With fewer than 2 points it shows the payload's `note`.
  - **Commit:** release, kind and writer, message, 12-character SHA, time.
  - **Health:** each container's state, health, restarts and OOM; the probe result.
  - **Runbook:** heading, source and a 280-character snippet.
  - A disproof query is labelled "disproof check".
- **Metric units:** C1 has no unit (issue 4 in `contract-review-a.md`), so the app picks one per template: % for error and failure rates, ms or s for latencies, cores for `cpu_usage` (the fixtures use 0.5 for a full slot), rps, MiB.
- **Unknown values:**
  - Any enum value this build does not know reads "Unknown". For severity it reads "SEV?".
  - An unknown evidence kind reads "Unknown evidence" and shows "This app version cannot show this evidence type." with its summary.

**Two bugs found by the tests and fixed before commit:** two highlighted log lines shared a widget key (Flutter threw "Duplicate keys"), and a payload missing an enum field threw an `ArgumentError` that the parser did not catch.

**Checks (2026-10-10):**
- `flutter analyze`: no issues.
- `flutter test` on `tanzeel`: **54 passed**, 3 skipped (the fixture groups).
  - Feed: 9 tests. They include MOB-003's acceptance: an `incident.opened` event is on screen one frame after the stream delivers it, with no refetch.
  - Detail: 9 tests. They include MOB-004's acceptance: on a 540 × 800 screen, `EvidenceCard(E3)` is off screen until the E3 chip is tapped, and visible after.
  - Evidence cards: 8 tests, one per variant plus empty log and empty metric.
  - Formatting: 3 tests.
- `OCP_CONTRACTS_DIR=<worktree of origin/usman 0582f37>/contracts flutter test`: **88 passed**.
  - The feed lists the 3 fixture incidents from `MockIncidentRepository`.
  - For each timeline's final incident, every evidence card renders its variant (never the fallback), every metric item draws a chart, and every cited `E#` chip scrolls its card into view.
  - The fixtures contain all six evidence kinds. None has `suspicious_content: true` or a dropped hypothesis, so those use the tests' own incident; the suspicious chip (MOB-019) is task A3.5.

**Phone check (Tanzeel, 2026-10-10):** the mock-mode APK works on an Android phone: sign-in, feed, incident detail, `E#` chips, charts, and the "Review proposed action" button. Two issues:
1. On metric charts the top two y-axis labels overlapped (for example "2.60 s" over "2.50 s"), and the lowest label touched the caption. **Fixed:** the y axis now starts at 0 and ends on a multiple of a round interval (1, 2, 2.5 or 5 × 10ⁿ), so fl_chart's end labels fall on grid lines; the chart has 8 px of padding above and below. A widget test checks the rendered labels (`0 %`, `10.0 %` … `40.0 %`, no extra peak label).
2. **Note:** the fixtures' evidence summary text embeds fixed clock times (for example "rose … at 10:15:00", "deployed at 08:58:40"), so in mock mode it will not match the shifted timestamps the cards show. The fixtures are unchanged.

**Debug APK for the phone** (checked on a device 2026-10-10, see above):
- Path: `D:\PROJECTS\OneCallpilot\mobile\build\app\outputs\flutter-apk\app-debug.apk` (`mobile/build/app/outputs/flutter-apk/app-debug.apk`), 220 MB (debug, all ABIs). Rebuilt after the chart fix (`fix(mobile)` commit of 2026-10-10): SHA-1 `2ae37389d6376450823ec7b626c89b70fb7dd8f5`; the build the phone check used was `30f9069600db3e47b2f82e43d0d9b7808e52be3f`.
- **Built in mock mode:** `flutter build apk --debug --dart-define=DATA_SOURCE=mock`, with the 3 timelines synced from `origin/usman` at `0582f37` and bundled in the APK. It needs no network: any email with a password of at least 6 characters signs in, and the incidents replay from the start on every launch.

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

## Phase 0 close-out (2026-10-09)

**Definition of done** (`phases.md`, Phase 0):

| Item | Status | Owner of what is left |
|---|---|---|
| All contracts merged and read by the other developer | **Not done.** Drafts only: C4, C6, C7 on `tanzeel` (read by Usman: no blocking issue); C1, C2, C3, C5, C8, C9 on `usman`, read by Tanzeel with 8 non-blocking issues in `docs/checks/contract-review-a.md`. Nothing is merged into `main`. | Tanzeel and Usman: split the contract commits into `contract/*` PRs and merge (Tanzeel decides when); Usman answers the review. |
| Python fixtures validate against the schemas | **Done on the branches.** `contracts/python` on `usman` (which includes C4, C6, C7): 246 tests pass, timelines and ws fixtures validate, schemas regenerate with no diff. Tanzeel's ledger fixture validates on `tanzeel` (36 tests). | Becomes "done on `main`" when the contract PRs merge. |
| Dart parses the fixtures (stub test) | **Done, pending merge.** `mobile/test/contracts/fixtures_test.dart` parses all 3 timelines, 14 WebSocket messages and the ledger fixture from `origin/usman` (21/21, run with `OCP_CONTRACTS_DIR`). On `tanzeel` the C1/C3 groups are skipped with a message until the fixtures are merged. | Goes green by itself once the C1/C3 fixtures are on `main`. |
| The phone reached the laptop | **Done** (S0.5, 2026-10-09, `docs/checks/network.md`). | — |
| Deliverable: `contracts-v0.1.0` tag | **Not done.** Needs the contract PRs merged. | Tanzeel (tag after the merge, when told). |
| Deliverable: green CI skeleton | **Workflows exist**; real checks in `contracts.yml` and `chaos-shop.yml`. The first CI result on the draft PR has not been checked here (no `gh` CLI). | Tanzeel: check the PR's checks, then set branch protection on `main`. |
| Deliverable: network spike result | **Done** (`docs/checks/network.md`). | — |
| Integration checkpoint: 30-minute fixture walkthrough | **Not done.** | Tanzeel and Usman. |

**Still open on Usman's side** (from `origin/usman`, `docs/checks/week-1.md`): S0.6 Docker Desktop on his laptop, B0.1 Supabase projects, B0.3 API keys, S0.7 (a) ro proxy with Alloy, (c) embeddings, (d) Supabase JWKS.

**Contract commits to split into `contract/*` PRs later** (each touches only `contracts/`): `1e513ad`, `85c23b6`, `886ef7c`, `c762ac6`, `bdb408c`, `13fb73f`, **`2910d52`** (C7 neutral logger names) and **`84d9cbd`** (C6 ledger fixture aligned with `releases.yaml`). `contracts/VERSION` stays 0.1.0 until the contracts are merged and tagged (ruling of 2026-10-09).

**Ledger fixture alignment (2026-10-09):** `contracts/fixtures/ledger/deploys.jsonl` now uses the commit SHAs, messages and config hashes from `chaos-shop/releases.yaml`, the seeded history's deploy times, and the reset reason `chaos reset` writes. Line order, kinds, writers and the 1.5.0 deploy time (which `test_ledger.py` relies on) are unchanged. Usman's contract suite from `origin/usman` (temporary worktree, outside the repository, with the changed fixture copied in): **246 passed**, none broken. The Dart fixture test against the same worktree: 21/21. A new CLI test fails if the fixture drifts from `releases.yaml` again.

**Merge notes for later:** `origin/usman` is built on `tanzeel` at `6cac236` (A1.1). Merging both into `main` will conflict in `compose.yaml` (each branch adds one `include:` line) and in `docs/checks/week-1.md` (both add sections); both are simple to resolve.

## Phase 1 status — Stream A (2026-10-10)

| Item | Status | Evidence |
|---|---|---|
| A1.1 Chaos Shop services, C7 telemetry | Done on `tanzeel` | A1.1 above |
| A1.2 `cs-lb`, slots, releases | Done on `tanzeel` | A1.2 above (TB-011 measured from app-ready) |
| A1.3 chaos CLI, ledger, 8 faults | Done on `tanzeel` | A1.3 above |
| A1.4 Spike calibrated (100 rps) | Done on `tanzeel` | A1.4 above |
| A1.5 Flutter skeleton, mock mode | Done on `tanzeel` | A1.5 above. Open: MOB-001 on a device against Supabase (needs B0.1) |
| A1.6 Feed and Detail | Done on `tanzeel`, checked on the phone | A1.6 above |
| TB-008 15-minute persistence | Done: 8/8 PASS overnight | "Overnight persistence run, result" above |
| S1.1 root `compose.yaml` and `ocp up` (shared) | `compose.yaml` includes testbed and runner; `ocp` CLI not started | `docs/checks/gate-g1.md`, criterion 1 |
| S1.2 first runbooks (shared) | Not started (`runbooks/` is empty) | TB-013; the TB-010 grep already covers `runbooks/` |
| Gate G1 | Criteria 2 and 3 met on `tanzeel`; 1, 4 and 5 open | `docs/checks/gate-g1.md` |

**Proposed split of the shared Phase 1 work** (proposal, not agreed yet):
- **S1.1, Tanzeel:**
  - `tools/ocp` as a uv tool: `ocp up` runs `docker compose --profile testbed --profile obs --profile copilot --profile runner up -d --build`, creates the stopped slots, runs `chaos reset`, waits for health and prints the LAN URL (architecture §12.1). A profile with no services yet is a no-op, so it works before Usman's files exist.
  - `ocp down`.
  - `ocp doctor`: ports, health, versions, and the published-port policy of SEC-013.
  - Nothing in it needs Usman's compose files.
- **S1.1, Usman:**
  - `observability.yml` (B1.5) and `copilot.yml`, each with its `include:` line in `compose.yaml`.
  - `ocp seed-incident` (B1.4).
  - `ocp smoke` later (Phase 2).
- **S1.1, both:** merge `tanzeel` and `usman` into `main` (after the contract PRs) so that `ocp up` on `main` brings everything up for G1.
- **S1.2:** 6–10 generic runbooks (TB-013), each reviewed by the developer who did not write it.
  - Tanzeel writes the testbed and runner side: restart a service, roll back a release, scale the API, crash loops, memory issues.
  - Usman writes cache issues, database connection issues, upstream dependency issues, and the escalation policy.
  - Usman's `search_runbooks` (AI-009) reads them, so he decides the final list and format.
- **Gate G1:**
  - Tanzeel owns criteria 2 and 3 (met on `tanzeel`) and the walkthrough run.
  - Usman owns 4 (Grafana and Loki; screenshots taken together) and 5 (detector, stretch).
  - Criterion 1 is shared through S1.1.

## Friday checkpoint (W1)

_To be filled in: stack boots with one command; phone reaches the laptop._
