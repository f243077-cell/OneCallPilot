# OnCallPilot

A mobile AI incident copilot. When an alert fires, a server-side agent investigates with **read-only** tools, ranks up to three root-cause hypotheses that cite stored evidence, checks its own answer, and either escalates or proposes **one** action from a fixed catalogue. The on-call engineer reviews the evidence and a dry run on an Android phone and approves (biometric for medium risk) or rejects. An isolated runner executes the approved action and the system verifies recovery, offering a rollback if it fails.

It is tested on **Chaos Shop**, a deliberately breakable microservice testbed with 8 fault scenarios, and judged by a benchmark of 48 runs (manual vs copilot) on time-to-diagnosis with zero unsafe actions.

Detailed documents: [`docs/project-requirements.md`](docs/project-requirements.md) (why), [`docs/architecture.md`](docs/architecture.md) (names, schemas, flows), [`docs/requirements.md`](docs/requirements.md) (testable IDs), [`docs/phases.md`](docs/phases.md) (plan). Development rules: [`CLAUDE.md`](CLAUDE.md).

## Architecture

```
alert ─▶ detector (backend-worker) ─▶ POST /ingest/alert (backend-api) ─▶ incident + agent job
                                                                              │
          agent (backend-worker): read-only tools ─▶ evidence E1…En ─▶ hypotheses ─▶ reflection
                                                                              │
                     proposal (catalogue-validated, signed dry run by the runner) ─▶ push
                                                                              │
     phone: challenge ─▶ biometric/tap ─▶ approve ─▶ signed execute ─▶ runner ─▶ result
                                                                              │
                   verification (structural + signals) ─▶ resolved, or rollback proposal
```

| Component | Folder | What it does |
|---|---|---|
| backend-api | `backend/oncallpilot/api`, `approvals`, `push` | REST + WebSocket, alert ingestion, the approval protocol, signed `execute`, pushes |
| backend-worker | `backend/oncallpilot/worker`, `agent`, `detector` | detector, agent investigations, runner results, verification, sweeper, auto-resolve |
| runner | `runner/` | the only component that changes containers, through `socket-proxy-rw` |
| app | `mobile/` | Flutter, Android: feed, evidence, action review, approval |
| testbed | `chaos-shop/` | api, worker, payments, cache, load generator, `chaos` CLI |
| observability | `infrastructure/observability/` | Prometheus, Loki, Alloy, Grafana |
| contracts | `contracts/` | the shared API, events, runner messages, catalogue, push payload |
| benchmark | `benchmark/` | `bench` CLI: plan, run, marks, audits, report |

Storage: Supabase Postgres (schema `ocp`, pgvector) and a private Redis (`copilot-redis`) for event, job and runner streams.

## Setup

1. Docker with Compose ≥ 2.20, Python 3.12 and `uv` on the host.
2. `cp .env.example .env` and fill in real values (never commit `.env`). Each variable has a one-line comment in `.env.example`.
3. `uv run --project tools/ocp ocp up` starts the testbed, observability, copilot-redis and the runner profile and resets the testbed; `ocp doctor` checks ports, health and versions.
4. backend-api and backend-worker run in the Compose profile `backend` once the Supabase project exists: `docker compose --profile backend up -d`.

Checks, per package (`backend/`, `runner/`, `contracts/python/`, `chaos-shop/`, `tools/ocp/`, `benchmark/`): `uv sync --locked`, `uv run ruff check .`, `uv run mypy`, `uv run pytest -q`. The backend's database, integration, regression and security suites need throwaway Postgres (`pgvector/pgvector`) and Redis containers (`OCP_TEST_DATABASE_URL`, `OCP_TEST_REDIS_URL`).

## Safety model

- **The LLM never controls infrastructure.** Its tools only read (logs, metrics, deploys, container state through a GET-only proxy, runbooks). It can propose at most one action, and only one from `contracts/actions.yaml`; parameters are enums built from `runner/targets.yaml` and the deploy ledger. Risk tier, approval requirement and rollback step come from the catalogue, never from the model.
- **A human approves every action.** A single-use challenge bound to the user, the proposal and its fingerprint; biometric for medium and high risk; idempotency keys; rate limits and cooldowns. No execution row can exist without an approved approval (database trigger).
- **Two signing keys.** `execute` is signed with `ACTION_SIGNING_KEY`, held only by backend-api and the runner; the worker, which talks to the LLM, holds only `RUNNER_LINK_KEY` (dry runs and results).
- **The runner validates again** (signature, expiry, catalogue, targets, idempotency, its own limits, state fingerprint) and can only start, stop and restart allowlisted containers through `socket-proxy-rw`; it cannot create, delete or exec.
- **Logs and tool output are untrusted.** They are redacted before storage, wrapped as untrusted data for the model, and flagged when they look like instructions.
- **Everything is audited**, append-only, in the same transaction as the change.

## Known limitations

These are accepted for v1 and listed in full in [`docs/architecture.md` §7.12](docs/architecture.md). None of them lets the agent or the runner act outside the allowlisted catalogue.

1. **Runner results are authenticated with a shared key** (`RUNNER_LINK_KEY`), so a compromised backend-worker could forge a runner *result*. It still cannot trigger an action (`execute` needs `ACTION_SIGNING_KEY`), and an incident resolves only when the worker's own signal verification passes too.
2. **Biometric approval is not server-verifiable:** `local_auth` returns a local boolean. The single-use nonce, fingerprint binding, rate limits and cooldown still apply.
3. **`socket-proxy-rw` filters by endpoint, not by container.** The runner enforces the label and name allowlist itself; create, exec and delete remain impossible.
4. **Cleartext HTTP and WebSocket on the LAN** in debug builds (ADR-16). Release builds require HTTPS; only port 8000 is exposed.
5. **Interrupted investigations are escalated, not resumed.** The evidence gathered so far stays and a human takes over.
6. **`socket-proxy-rw` also allows `GET /containers/{id}/attach/ws`** (stdio of a running container). The monitored containers do not read commands from stdin; a security test fails if the endpoint's behaviour changes.
7. **Container inspect exposes environment variables** to a compromised runner. The agent's health tool never reads them, and secrets held in mounted files are not exposed.
8. **The Claude fallback model cannot run at temperature 0** (current Claude models reject sampling parameters), so a benchmark that falls back to it (ADR-13) is less repeatable than one on the primary model.

## Results

The benchmark report is generated by `bench report` into [`benchmark/results/report.md`](benchmark/results/); see [`benchmark/PROCEDURE.md`](benchmark/PROCEDURE.md). Results are pending the benchmark runs.
