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
- `cs-loadgen` sends to `api-upstream:8000` directly until A1.2 adds `cs-lb`.
- cs-api and cs-payments do not access-log `/metrics` and `/internal/*`, so admin calls never appear in agent-visible logs. cs-payments request lines have no `route` field (C7 route values name cs-api routes).

## Friday checkpoint (W1)

_To be filled in: stack boots with one command; phone reaches the laptop._
