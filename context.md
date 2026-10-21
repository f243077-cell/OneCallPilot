# Context — current state

> Rewritten at the end of every session (or chunk) so it matches reality (`CLAUDE.md` §11.4). Read it right after `CLAUDE.md`, then the newest `handoff.md` entry, then **`next.md`**.
> **Last updated:** 2026-10-21, cloud session 1 (chunk 10).

## Who and where
- **Tanzeel** is the only developer since 2026-10-21 (Usman has left). Sessions work for him and may edit every folder.
- Repository `https://github.com/f243077-cell/OneCallPilot`; all work is committed and pushed **directly to `main`** (`CLAUDE.md` §11). Sessions may run in the cloud while his laptop is off.

## Branches
| Branch | State |
|---|---|
| `main` | Everything: both streams' Phase 0, Stream A Phase 1 and Phase 2 up to A2.5 (PRs #1, #2), plus Stream B work from 2026-10-21. |
| `tanzeel`, `usman` | Fully merged into `main`; to be deleted by hand (the cloud session's delete got HTTP 403). |

## Phase
- **Stream A (testbed, runner, app):** Phase 2, A2.1–A2.5 done (see `docs/checks/week-3.md`, `gate-g2.md`).
- **Stream B (backend, agent, DB, observability, benchmark):** Phase 1 done (B1.1–B1.8); Phase 2 B2.1–B2.5 and B2.7–B2.11 done, plus FR-011 auto-resolve and the worker entry point (`python -m oncallpilot.worker`). B2.6 (recorded fixtures) needs the live testbed; B3.2 endpoints and B2.12 (regression suite, synthetic fixtures) done too; B2.6 recordings and the recorded-model replay need the live testbed and the Gemini key. See `next.md`.
- Phase 0 leftovers: the `contracts-v0.1.0` tag (on Tanzeel's say-so); S0.7 (c) embeddings and (d) Supabase JWKS need the Gemini key and Supabase (Tanzeel, by hand).

## How to run the checks
- Contracts (`contracts/python`): `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy oncallpilot_contracts tests`, `uv run pytest -q`, then `uv run python -m oncallpilot_contracts.schema_export` and `git diff --exit-code -- ../schemas`.
- Backend (`backend`): `uv sync --locked`, ruff, format, `uv run mypy`.
- Database suite: from `backend`, `OCP_TEST_DATABASE_URL=… uv run pytest -c pyproject.toml ../tests/db -q` against a `pgvector/pgvector:0.8.1-pg17` container (`next.md` §1).
- Every lockfile: `uv lock --check` in `contracts/python`, `backend`, `chaos-shop`, `chaos-shop/cli`, `runner`, `tools/ocp` (uv 0.12.7, as in CI).

## Cloud container notes
- uv 0.12.7 is at `/usr/local/bin/uv`; Docker's daemon is not running at start (`dockerd &` works); Docker Hub answers 429, so use `mirror.gcr.io/...`.
- `git push --delete` is refused by the session proxy (403). There is no `gh` CLI; use the GitHub MCP tools for CI status.

## Decisions (and where they are written)
- Single developer, direct pushes to `main` (`CLAUDE.md` §8.1, §11).
- `ocp_backend` is `NOLOGIN BYPASSRLS` in the migration; the login and password are set by hand per Supabase project. RLS stays on with no policies (`supabase/migrations/20261021190000_initial_schema.sql`).
- `audit_log` also refuses TRUNCATE (a statement trigger), besides UPDATE and DELETE.
- Migration CI: forward-only check against the previous head of `main`; names `<14 digits>_<snake>.sql`; Postgres 17 (`pgvector/pgvector:0.8.1-pg17`).
- Earlier contract decisions (snake_case schemas, `msg` stream field, HMAC over the JSON as sent, hex keys, `proposal.fingerprint` formula, the 4 KB evidence cut) are unchanged; see `docs/checks/contract-review-*.md`.
