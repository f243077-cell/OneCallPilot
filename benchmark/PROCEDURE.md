# Benchmark procedure

> Requirements: BENCH-002…BENCH-007 (`docs/requirements.md`). Scenarios: `scenarios.yaml`.
> Results: `results/runs.csv` (schema `schema/runs.csv.md`). Never edit result files by hand.

## Roles (BENCH-003)
- **Operator** handles the incident. They never see the plan entry, `scenarios.yaml`, or any chaos CLI output during a run.
- **Observer** runs `bench run`, which injects the fault, and records marks with `bench mark`. The observer is never the operator for the same run (the plan enforces it).
- Two people are needed. The plan (`bench plan --people a,b`) balances who operates which scenario in which mode.

## Before a session
1. On the integration host (Tanzeel's laptop), on the commit to benchmark (after `bench-freeze` for the real runs), with `.env` complete: `ocp up` and `ocp doctor` pass.
2. The environment of the observer's shell has `DATABASE_URL`, `REDIS_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `OCP_TEST_EMAIL`, `OCP_TEST_PASSWORD`; optionally `OCP_API_URL` (default `http://127.0.0.1:8000`) and `BENCH_CHAOS_CMD` (default `uv run --project chaos-shop/cli chaos`). Run `bench` from the repository root: `uv run --project benchmark bench …`.
3. Once per benchmark: `bench plan --seed <int> --people <a>,<b>` writes `results/plan-<seed>.csv` (48 runs). Commit the plan before the first run.
4. Set the mode for the next block of runs: `AGENT_ENABLED=false` (manual) or `true` (copilot) in `.env`, then `ocp up` again. `bench run` refuses a run whose mode does not match (BENCH-005).

## One run
1. Observer: `bench run <run_id> --plan results/plan-<seed>.csv`. It resets the testbed, resolves leftovers as `bench_cleanup` (or aborts), clears rate limits and cooldowns, stamps the run, waits 4 minutes (warm-up + baseline), injects, and waits for the run's own incident.
2. Operator works the incident with **only** the tools of the mode (below).
3. Observer marks, as they happen:
   - `bench mark diagnosis "<what the operator said>"` — when the operator states the root cause aloud (both modes).
   - `bench mark decision` — manual mode: when the operator starts the fix.
   - `bench mark action-correct true|false` — manual mode: whether the fix applied was the expected one.
   - `bench mark note "<text>"` — anything unusual (in copilot mode: Grafana opened after rejection or escalation).
4. `bench run` ends when the incident is resolved, or escalated where escalation is the expected outcome (scenarios 7, 8), or after 20 minutes, and appends one row to `results/runs.csv`.
5. An abort (busy or stuck leftover, mode mismatch, failed stack command) writes **no** row; fix the cause and run the same `run_id` again. A row with `notes = invalid_run` (an alert older than the injection) is repeated with a new run.

## Manual mode tools (BENCH-005)
`AGENT_ENABLED=false`. Grafana (dashboards and Explore), a terminal on the host (`docker` CLI, the deploy ledger file, the `runbooks/` folder), and the push alert. The operator recovers with `docker` commands.

## Copilot mode tools (BENCH-006)
`AGENT_ENABLED=true`. The phone only. Grafana only after rejecting the proposal or after the incident escalated; the observer records any such use with `bench mark note`.

## After the session
`bench check` validates `results/runs.csv`. Commit `results/` (plan, rows, `marks-*.json`).
