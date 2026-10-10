# Gate G1 — "all 8 faults run from one command" (end of Week 2)

> Checklist for `phases.md` Phase 1, Gate G1. Status as of **2026-10-10**. Evidence so far comes from branch `tanzeel`; nothing is merged into `main` yet, so the gate walkthrough runs from `main` only after the merges.
> Owners: **T** = Tanzeel (Stream A), **U** = Usman (Stream B), **T+U** = shared.

| # | Criterion | Status | Evidence we have | Missing, and who owns it |
|---|---|---|---|---|
| 1 | `ocp up` on Tanzeel's laptop brings everything to healthy | **Not met** | The testbed part is healthy with plain Compose: `docker compose --profile testbed up -d --wait` plus `--profile testbed-slots create` (A1.1, A1.2; `week-1.md`). `socket-proxy-rw` is in `runner.yml`. | **T+U (S1.1):** the `ocp` CLI in `tools/ocp/` (`up`, `down`, `doctor`) does not exist yet. **U:** `observability.yml` is on `origin/usman` (only `socket-proxy-ro` so far); Prometheus, Loki, Alloy and Grafana come with B1.5. **T+U:** merge both branches into `main` (the `compose.yaml` `include:` lines conflict trivially). |
| 2 | `chaos inject <n>` works for all 8 scenarios; `chaos reset` restores the baseline in ≤ 90 s | **Met on `tanzeel`** | **Inject (TB-006, limit 30 s):** every scenario, 0.3–13.7 s over four runs (A1.3 verify, A1.4 `verify --all`, overnight run). **Reset (TB-007, limit 90 s):** 9.9–21 s in every run; after reset `chaos status` shows the baseline, no switch is on, and the ledger ends with the two `kind=reset` records. **Live tests** (`chaos-shop/cli`, `pytest -m live -k "not verify"`): 10 passed (A1.3), covering reset ≤ 90 s, each inject ≤ 30 s and shown by `status`, and a valid, neutral ledger. | Re-run from `main` at the walkthrough (**T**). |
| 3 | `chaos verify 1..6` passes; scenarios 7 and 8 persist for 15 minutes | **Met on `tanzeel`** | **Overnight run, 2026-10-09** (`chaos verify --all --hold 900`, 127 min): **8/8 PASS**. Scenarios 1–6 stayed broken for 15 minutes and recovered 2–43 s after their fix (limit 120 s); 7 and 8 stayed broken for 15 minutes and need escalation. Table in `week-1.md`, "Overnight persistence run, result". Earlier run with a 180 s hold (A1.4): 8/8 PASS. | Re-run `verify --all --hold 180` from `main` at the walkthrough (**T**). |
| 4 | Each scenario is visible in Grafana or Loki Explore (screenshots in this file) | **Not met** | The testbed emits the C7 metrics and logs every dashboard needs (TB-003, TB-004: contract tests on `/metrics` and on 1,000+ log lines). Each scenario's signal, from the verify runs: 1: worker RSS and restarts; 2: checkout 500s; 3: pool timeouts; 4: 503s with cs-redis down; 5: request rate ×18 and p95 ~9 s; 6: worker crash loop; 7: checkout p95 ~2.6 s; 8: coupon 500s. | **U (B1.5):** Prometheus, Loki, Alloy and Grafana with the "Chaos Shop Overview" dashboard (TB-012). **T+U:** one screenshot per scenario, with Tanzeel injecting. Note for the dashboard: scenario 5 shows best as p95 and request rate, not error rate (`week-1.md`). |
| 5 | The detector opens an incident for at least scenarios 2, 4 and 6 (stretch for G1, required for G2) | **Not met** (stretch) | Faults 2, 4 and 6 are reproducible and persist (criterion 3); their signals are error rate (2, 4) and restarts (6). | **U (B1.6, B1.7):** `POST /ingest/alert` and detector v1 against the live stack, which needs criterion 4's Prometheus. |

## Integration checkpoints

| Checkpoint | Status | Notes |
|---|---|---|
| Friday W1: stack boots; phone reaches the laptop | Partly | The phone reached the laptop (S0.5, `network.md`). The testbed boots; "one command" waits for `ocp up` (criterion 1). The phone also runs the mock-mode app (A1.6). |
| Friday W2: Gate G1 walkthrough | Not started | Needs criteria 1 and 4, and the merges into `main`. |

## Walkthrough script (when 1 and 4 are ready)

1. On `main`: `ocp up`; confirm every service is healthy and `ocp doctor` passes.
2. `uv run --project chaos-shop/cli chaos verify --all --hold 180` (about 31 minutes), with Grafana open; take one screenshot per scenario while it is broken.
3. Paste the PASS table and the screenshots below.

## Screenshots

_None yet (criterion 4)._
