# Gate G1 — "all 8 faults run from one command" (end of Week 2)

> Checklist for `phases.md` Phase 1, Gate G1. First written 2026-10-10 from branch `tanzeel`. **Status 2026-11-15: met.** The walkthrough ran from `main` (`7cc169a`) on Tanzeel's laptop; results and screenshots are in "Walkthrough from `main`, 2026-11-15" below. The rows keep the 2026-10-10 evidence and add the walkthrough.
> Owners: **T** = Tanzeel (Stream A), **U** = Usman (Stream B), **T+U** = shared.

| # | Criterion | Status | Evidence we have | Missing, and who owns it |
|---|---|---|---|---|
| 1 | `ocp up` on Tanzeel's laptop brings everything to healthy | **Met** (2026-11-15, from `main`): see the walkthrough. Before: partly met, the testbed and runner profiles on `tanzeel` | **`ocp up` works (2026-10-10).** It starts profiles `testbed`, `obs`, `copilot` and `runner` (the empty ones select nothing), creates the stopped slots, runs `chaos reset`, waits for health and prints the LAN URLs. Live: from no containers (`ocp down` first) to 8 services healthy in **37 s**; a repeat `ocp up` takes 14 s; `ocp up` with a build takes 49 s. **`ocp doctor` passes:** Docker 29.8.1, Compose 5.5.1, 8 services healthy, published ports match architecture §3 (only `cs-lb` on 127.0.0.1:8080), only `socket-proxy-rw` mounts the Docker socket, 18 images pinned, no other running container open to the LAN. Tests: `tools/ocp`, 24 unit and 2 live, all passed. | **U:** the observability stack (B1.5: Prometheus, Loki, Alloy, Grafana) and later `copilot.yml`. `ocp up` starts them with no change, because their profiles are already passed. **T+U:** merge both branches into `main` and run `ocp up` and `ocp doctor` from `main`. **Not yet tried:** `uv tool install ./tools/ocp` (NFR-007), because it installs a tool for the user; `uv run --project tools/ocp ocp …` was used instead. |
| 2 | `chaos inject <n>` works for all 8 scenarios; `chaos reset` restores the baseline in ≤ 90 s | **Met** (2026-11-15, from `main`: inject 0.5–12.0 s, reset 12.4–35.9 s). Before: met on `tanzeel` | **Inject (TB-006, limit 30 s):** every scenario, 0.3–13.7 s over four runs (A1.3 verify, A1.4 `verify --all`, overnight run). **Reset (TB-007, limit 90 s):** 9.9–21 s in every run; after reset `chaos status` shows the baseline, no switch is on, and the ledger ends with the two `kind=reset` records. **Live tests** (`chaos-shop/cli`, `pytest -m live -k "not verify"`): 10 passed (A1.3), covering reset ≤ 90 s, each inject ≤ 30 s and shown by `status`, and a valid, neutral ledger. | Re-run from `main` at the walkthrough (**T**). |
| 3 | `chaos verify 1..6` passes; scenarios 7 and 8 persist for 15 minutes | **Met** (2026-11-15, from `main`, `--hold 180`: 8/8 after a re-run of scenario 5, see the walkthrough). Before: met on `tanzeel`, overnight with `--hold 900` | **Overnight run, 2026-10-09** (`chaos verify --all --hold 900`, 127 min): **8/8 PASS**. Scenarios 1–6 stayed broken for 15 minutes and recovered 2–43 s after their fix (limit 120 s); 7 and 8 stayed broken for 15 minutes and need escalation. Table in `week-1.md`, "Overnight persistence run, result". Earlier run with a 180 s hold (A1.4): 8/8 PASS. | Re-run `verify --all --hold 180` from `main` at the walkthrough (**T**). |
| 4 | Each scenario is visible in Grafana or Loki Explore (screenshots in this file) | **Met** (2026-11-15): one "Chaos Shop Overview" screenshot per scenario, below | The testbed emits the C7 metrics and logs every dashboard needs (TB-003, TB-004: contract tests on `/metrics` and on 1,000+ log lines). Each scenario's signal, from the verify runs: 1: worker RSS and restarts; 2: checkout 500s; 3: pool timeouts; 4: 503s with cs-redis down; 5: request rate ×18 and p95 ~9 s; 6: worker crash loop; 7: checkout p95 ~2.6 s; 8: coupon 500s. | **U (B1.5):** Prometheus, Loki, Alloy and Grafana with the "Chaos Shop Overview" dashboard (TB-012). **T+U:** one screenshot per scenario, with Tanzeel injecting. Note for the dashboard: scenario 5 shows best as p95 and request rate, not error rate (`week-1.md`). |
| 5 | The detector opens an incident for at least scenarios 2, 4 and 6 (stretch for G1, required for G2) | **Met** (2026-11-08): the detector opened the run's own incident for all 8 scenarios in the Gate G2 run, 0.7–204.9 s after the injection (`gate-g2.md`) | Faults 2, 4 and 6 are reproducible and persist (criterion 3); their signals are error rate (2, 4) and restarts (6). | **U (B1.6, B1.7):** `POST /ingest/alert` and detector v1 against the live stack, which needs criterion 4's Prometheus. |

> **Note (2026-10-10):** `socket-proxy-rw`, which `ocp up` starts with the `runner` profile, is now security-tested against the real proxy (TEST-015, 54/54, also in CI): see `docs/checks/proxy.md` and Gate G2 criterion 3 in `docs/checks/gate-g2.md`.

## Integration checkpoints

| Checkpoint | Status | Notes |
|---|---|---|
| Friday W1: stack boots; phone reaches the laptop | Met for Stream A | The phone reached the laptop (S0.5, `network.md`). The testbed and runner boot with one command (`ocp up`, criterion 1). The phone also runs the mock-mode app (A1.6). Usman's stack is not in `ocp up` yet. |
| Friday W2: Gate G1 walkthrough | Done 2026-11-15 | From `main`, below. |

## Walkthrough script (when 1 and 4 are ready)

1. On `main`: `ocp up`; confirm every service is healthy and `ocp doctor` passes.
2. `uv run --project chaos-shop/cli chaos verify --all --hold 180` (about 31 minutes), with Grafana open; take one screenshot per scenario while it is broken.
3. Paste the PASS table and the screenshots below.

## Walkthrough from `main`, 2026-11-15 (Tanzeel)

Commit `7cc169a`, Docker 29.8.2, Compose 5.6.0, laptop clock and Docker clock agree (UTC), Pakistan Standard Time.

1. **`ocp up --no-backend`:** 15 services healthy in 122 s, including the `obs` profile (Prometheus, Loki, Alloy, Grafana), copilot-redis, the runner and both socket proxies; `chaos reset` 35.9 s. The backend was left out on purpose: it would write to the shared Supabase `ocp-dev` (about to move region) and could call the LLM. backend-api and backend-worker are proven healthy by the live smoke runs (`smoke.md`) and the Gate G2 run.
2. **`ocp doctor`:** passes every check except the one that expects backend-api and backend-worker to run (left out, step 1): Docker and Compose versions, published ports match the architecture table, only the socket proxies mount the Docker socket, 27 images pinned, nothing else open to the LAN.
3. **`chaos verify --all --hold 180`** (34 min), with Grafana open in a browser that took the screenshots:

| Scenario | Broken after 180 s | Fix | Result |
|---|---|---|---|
| 1 memory leak | rss 239 MiB, retention on | restart worker | PASS, recovered 17 s after the fix |
| 2 bad deploy | checkout 10 × 500 on api 1.5.0 | api back to 1.4.0 | PASS, 3 s |
| 3 db pool | 11 pool timeouts in 10 s, `GET /orders` 503s | restart api | PASS, 13 s |
| 4 cache outage | cs-redis exited, checkout 503 | start cs-redis | PASS, 11 s |
| 5 traffic spike | 38 rps served of 100, 5xx 16.9 %, p95 9.8 s | scale api to 3 | **FAIL** in this run: after 120 s 5xx 0.0 % and 82 rps, but p95 365 ms (recovered needs < 300 ms) |
| 6 config crash | worker 2.2.0 crash loop, 18 restarts | worker back to 2.1.0 | PASS, 10 s |
| 7 slow dependency | checkout 2.68–2.78 s | none (escalate) | PASS, persisted |
| 8 log injection | crafted coupon stream, 21 × 500 in 10 s | none (escalate) | PASS, persisted |

4. **Scenario 5 again, alone** (`chaos verify 5 --hold 180`, browser closed): broken (65 rps of 100, 5xx 48.3 %, p95 10.0 s); after scaling to 3, **PASS, recovered 75 s after the fix** (97 rps, 5xx 0.0 %, p95 238 ms).

**Scenario 5 margin (a risk for the benchmark, not a gate failure).** Earlier runs recovered 2–43 s after the fix; this time 75 s alone, and not within 120 s while a browser refreshed the Grafana dashboard every 10 s during the run. Three api replicas at 100 rps sit close to the 300 ms p95 line on this laptop, so extra load on the host can keep the copilot's signal verification from passing in time. Rule for the pilot and the counted runs: nothing else runs on the laptop, and the Grafana dashboard is not left open on the host during copilot runs (the manual operator uses it only in manual mode). Watch scenario 5 in the pilot (P4.1); if it misses verification there, report it before the freeze rather than change the threshold.

## Screenshots

"Chaos Shop Overview", last 10 minutes, taken about 120 s after each injection.

| Scenario | Panels to read (the signal the verify run measured) |
|---|---|
| 1 memory leak | memory RSS (worker) |
| 2 bad deploy | api error rate, releases running |
| 3 db pool | db pool in use and wait p95 |
| 4 cache outage | cache errors per second, api error rate |
| 5 traffic spike | api requests per second, api p95 latency, api error rate |
| 6 config crash | process restarts, releases running |
| 7 slow dependency | upstream (payments) p95 latency, api p95 latency |
| 8 log injection | api error rate, logs panel |

![Scenario 1](img/gate-g1-s1.png)
![Scenario 2](img/gate-g1-s2.png)
![Scenario 3](img/gate-g1-s3.png)
![Scenario 4](img/gate-g1-s4.png)
![Scenario 5](img/gate-g1-s5.png)
![Scenario 6](img/gate-g1-s6.png)
![Scenario 7](img/gate-g1-s7.png)
![Scenario 8](img/gate-g1-s8.png)
