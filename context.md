# Context — current state

> Rewritten at the end of every session (or chunk) so it matches reality (`CLAUDE.md` §11.4). Read it right after `CLAUDE.md`, then the newest `handoff.md` entry, then **`next.md`**.
> **Last updated:** 2026-11-02, local session on the laptop.

## Who and where
- **Tanzeel** is the only developer since 2026-10-21 (Usman has left). Sessions work for him and may edit every folder.
- Repository `https://github.com/f243077-cell/OneCallPilot`; all work is committed and pushed **directly to `main`** (`CLAUDE.md` §11). Sessions may run in the cloud while his laptop is off.

## Branches
| Branch | State |
|---|---|
| `main` | Everything: both streams' Phase 0, Stream A Phase 1 and Phase 2 up to A2.5 (PRs #1, #2), plus Stream B work from 2026-10-21. |
| `tanzeel`, `usman` | Deleted 2026-11-02 (both were fully merged). |

## Phase
- **Stream A (testbed, runner, app):** Phase 2, Phase 2 done, A2.1–A2.9 (A2.1–A2.5: `docs/checks/week-3.md`, `gate-g2.md`; A2.6–A2.9 from 2026-10-21); Phase 3: A3.1, A3.4, A3.5 done; A3.6 done; S3.1 done (runner live crash test); A3.2/A3.3/A3.7/A3.8 need Firebase and the phone.
- **Stream B (backend, agent, DB, observability, benchmark):** Phase 1 done (B1.1–B1.8); Phase 2 B2.1–B2.5 and B2.7–B2.11 done, plus FR-011 auto-resolve and the worker entry point (`python -m oncallpilot.worker`). B2.6 (recorded fixtures) needs the live testbed; B3.2 endpoints, B2.12 (regression suite, synthetic fixtures) B3.8 (Claude adapter) and B3.6–B3.7 (benchmark harness, `benchmark/`) B3.5 (copilot metrics), the backend Compose services and the README (S3.2 draft, SEC-018) done too; B2.6 recordings and the recorded-model replay need the live testbed and the Gemini key. See `next.md`.
- Phase 0: done. S0.7 (c) embeddings (through OpenRouter) and (d) Supabase JWKS passed on 2026-11-02 (`docs/checks/proxy.md`); `contracts-v0.1.0` is tagged.
- **Supabase `ocp-dev`** is live and migrated (2026-11-02); the LLM and embeddings go through OpenRouter (ADR-23, `LLM_PROVIDER=openrouter`, `google/gemini-3.8-flash`).

## How to run the checks
- Contracts (`contracts/python`): `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy oncallpilot_contracts tests`, `uv run pytest -q`, then `uv run python -m oncallpilot_contracts.schema_export` and `git diff --exit-code -- ../schemas`.
- Backend (`backend`): `uv sync --locked`, ruff, format, `uv run mypy`.
- Database suite: from `backend`, `OCP_TEST_DATABASE_URL=… uv run pytest -c pyproject.toml ../tests/db -q` against a `pgvector/pgvector:0.8.1-pg17` container (`next.md` §1).
- Every lockfile: `uv lock --check` in `contracts/python`, `backend`, `chaos-shop`, `chaos-shop/cli`, `runner`, `tools/ocp` (uv 0.12.7, as in CI).

## Laptop notes
- Windows, Git Bash and PowerShell; uv 0.12.7. Docker Desktop is often off: start it before suites that need containers. 16 GB RAM, Docker 10 GB. Docker Desktop logs with the `local` driver (the chaos helper forces `json-file`).
- Windows time zone must be Pakistan Standard Time with automatic time: on 2026-11-02 it was Pacific, so Windows UTC (and for a while the Docker VM) was 13 h ahead. After any clock jump, restart the whole stack; Prometheus/Loki volumes may need recreating.
- The full stack (`ocp up`, backend included) uses about 0.8 GiB.
- Supabase setup script (one-off, not in the repo): applies new migrations and records them in `supabase_migrations.schema_migrations`; for `ocp-bench`, repeat with that project's values.

## Cloud container notes (obsolete since 2026-11-02)
- uv 0.12.7 is at `/usr/local/bin/uv`; Docker's daemon is not running at start (`dockerd &` works); Docker Hub answers 429, so use `mirror.gcr.io/...`.
- `git push --delete` is refused by the session proxy (403). There is no `gh` CLI; use the GitHub MCP tools for CI status.

## Decisions (and where they are written)
- Single developer, direct pushes to `main` (`CLAUDE.md` §8.1, §11).
- `ocp_backend` is `NOLOGIN BYPASSRLS` in the migration; the login and password are set by hand per Supabase project. RLS stays on with no policies (`supabase/migrations/20261021190000_initial_schema.sql`).
- `audit_log` also refuses TRUNCATE (a statement trigger), besides UPDATE and DELETE.
- Migration CI: forward-only check against the previous head of `main`; names `<14 digits>_<snake>.sql`; Postgres 17 (`pgvector/pgvector:0.8.1-pg17`).
- Earlier contract decisions (snake_case schemas, `msg` stream field, HMAC over the JSON as sent, hex keys, `proposal.fingerprint` formula, the 4 KB evidence cut) are unchanged; see `docs/checks/contract-review-*.md`.
