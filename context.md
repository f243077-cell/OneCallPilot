# Context — current state

> Rewritten at the end of every session (or chunk) so it matches reality (`CLAUDE.md` §11.4). Read it right after `CLAUDE.md`, then the newest `handoff.md` entry, then **`next.md`**.
> **Last updated:** 2026-11-08, Usman's session on his laptop (Stream B line, Usman's laptop notes and two decisions changed; the rest is Tanzeel's 2026-11-04 text).

## Who and where
- **Two developers since 2026-11-04:** Tanzeel (alone from 2026-10-21 to 2026-11-03) and Usman, who is back. Both may edit every folder; a session works for the developer who started it.
- **Split:** Usman does the tasks that need the real LLM (OpenRouter, his own key): `ocp smoke` live, recorded-model replay, Gate G2, B2.13, B3.3, B3.4, B3.9. Tanzeel does the rest (phone, testbed, runner, CI, docs). A3.3, A3.7, Gate G3 and Phase 4 run together on Tanzeel's laptop (`CLAUDE.md` §11.6).
- Repository `https://github.com/f243077-cell/OneCallPilot`; all work is committed and pushed **directly to `main`**, with `git pull --rebase origin main` before each push (`CLAUDE.md` §11). Both backends use Supabase `ocp-dev`: agree before running the full stack.

## Branches
| Branch | State |
|---|---|
| `main` | Everything: both streams' Phase 0, Stream A Phase 1 and Phase 2 up to A2.5 (PRs #1, #2), plus Stream B work from 2026-10-21. |
| `tanzeel`, `usman` | Deleted 2026-11-02 (both were fully merged). |

## Phase
- **Stream A (testbed, runner, app):** Phase 2, Phase 2 done, A2.1–A2.9 (A2.1–A2.5: `docs/checks/week-3.md`, `gate-g2.md`; A2.6–A2.9 from 2026-10-21); Phase 3: A3.1, A3.4, A3.5 done; A3.6 done; S3.1 done (runner live crash test); A3.2 done 2026-11-03 (push on the real phone, `docs/checks/push.md`; the app-killed case passed 2026-11-04); Gate G3 criterion 2 (Wi‑Fi off) passed on the device 2026-11-04 (`docs/checks/gate-g3.md`); A3.3 needs a proposal (OpenRouter credit); A3.7/A3.8 later.
- **Stream B (backend, agent, DB, observability, benchmark):** Phase 1 done (B1.1–B1.8); Phase 2 B2.1–B2.5 and B2.7–B2.11 done, plus FR-011 auto-resolve and the worker entry point (`python -m oncallpilot.worker`). B2.6 (recorded fixtures) needs the live testbed; B3.2 endpoints, B2.12 (regression suite, synthetic fixtures) B3.8 (Claude adapter) and B3.6–B3.7 (benchmark harness, `benchmark/`) B3.5 (copilot metrics), the backend Compose services and the README (S3.2 draft, SEC-018) done too; B3.1 tests done 2026-11-04; B2.6 tool recordings done 2026-11-03 (`tests/agent_regression/scenarios/recorded/`, `record.py`); **`ocp smoke` live passed 2026-11-07/08 on Usman's laptop with prompts v2** (S2.1, TEST-014; `docs/checks/smoke.md`); the Gate G2 run of all 8 scenarios started 2026-11-08; the recorded-model replay, B2.13, B3.3, B3.4 and B3.9 are next (Usman's own OpenRouter key). See `next.md`.
- Phase 0: done. S0.7 (c) embeddings (through OpenRouter) and (d) Supabase JWKS passed on 2026-11-02 (`docs/checks/proxy.md`); `contracts-v0.1.0` is tagged.
- **Supabase `ocp-dev`** is live and migrated (2026-11-02); the LLM and embeddings go through OpenRouter (ADR-23, `LLM_PROVIDER=openrouter`, `google/gemini-3.8-flash`).

## How to run the checks
- Contracts (`contracts/python`): `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy oncallpilot_contracts tests`, `uv run pytest -q`, then `uv run python -m oncallpilot_contracts.schema_export` and `git diff --exit-code -- ../schemas`.
- Backend (`backend`): `uv sync --locked`, ruff, format, `uv run mypy`.
- Database suite: from `backend`, `OCP_TEST_DATABASE_URL=… uv run pytest -c pyproject.toml ../tests/db -q` against a `pgvector/pgvector:0.8.1-pg17` container (`next.md` §1).
- Every lockfile: `uv lock --check` in `contracts/python`, `backend`, `benchmark`, `chaos-shop`, `chaos-shop/cli`, `runner`, `tools/ocp` (uv 0.12.7, as in CI; `git ls-files '*uv.lock'` lists them).

## Laptop notes
- Windows, Git Bash and PowerShell; uv 0.12.7. Docker Desktop is often off: start it before suites that need containers. 16 GB RAM, Docker 10 GB. Docker Desktop logs with the `local` driver (the chaos helper forces `json-file`).
- Windows time zone must be Pakistan Standard Time with automatic time: on 2026-11-02 it was Pacific, so Windows UTC (and for a while the Docker VM) was 13 h ahead. After any clock jump, restart the whole stack; Prometheus/Loki volumes may need recreating.
- The full stack (`ocp up`, backend included) uses about 0.8 GiB.
- Phone: Sparx Note 20 on Wi‑Fi SPARTACUS; the laptop's Wi‑Fi IP was 192.168.0.103, then 192.168.0.105 (DHCP). Firebase project `oneclickpilot-5a130`: `mobile/android/app/google-services.json` and `infrastructure/secrets/fcm-service-account.json`, both git-ignored. Local Flutter is 3.47.7; CI's 3.44.2 is at `D:lutter-sdks.44.2`.
- The laptop's internet drops now and then (2026-11-03: twice): the API answers 500 while Supabase is unreachable, then recovers by itself. Long live runs must retry (the B2.6 recorder does).
- To rebuild the backend image after a code change: `docker compose --profile tools --profile copilot --profile backend build backend-seed`, then `docker compose --profile copilot --profile backend up -d --wait --force-recreate backend-api backend-worker`.
- Supabase setup script (one-off, not in the repo): applies new migrations and records them in `supabase_migrations.schema_migrations`; for `ocp-bench`, repeat with that project's values.

## Usman's laptop notes
- Windows 10 Home, 8 cores, 20 GB RAM; C: is small (12 GB free), so Docker's disk image is on `G:\DockerData`. If WSL fails with `E_ACCESSDENIED` on `ext4.vhdx`, quit Docker Desktop, `wsl --shutdown`, and start it again (or restart Windows).
- Docker Desktop's `bin` is not on Git Bash's PATH: prepend `/c/Users/Usman Ghani/AppData/Local/Programs/DockerDesktop/resources/bin`.
- Local Flutter is 3.47.2: VS Code's Flutter extension rewrites `mobile/pubspec.lock`; revert it (`git checkout -- mobile/pubspec.lock`).
- Live runs need an idle laptop (a game made cs-api's 3 s healthchecks time out) and 30 healthy minutes before each injection (the tools' look-back).
- `ocp up` from scratch: about 5 minutes, then 17 services healthy.
- On this laptop `tests/integration/test_agent_jobs.py::test_two_workers_and_a_duplicate_job_make_one_investigation` fails (timing), also on code before Usman's changes; it passes in CI.

## Cloud container notes (obsolete since 2026-11-02)
- uv 0.12.7 is at `/usr/local/bin/uv`; Docker's daemon is not running at start (`dockerd &` works); Docker Hub answers 429, so use `mirror.gcr.io/...`.
- `git push --delete` is refused by the session proxy (403). There is no `gh` CLI; use the GitHub MCP tools for CI status.

## Decisions (and where they are written)
- Prompts v2 (2026-11-07): v1 plus the citation rules in `hypothesize.md`; a test keeps the table equal to `reflection_rules.yaml`. Default still v1 until Tanzeel and Usman decide.
- Evidence that a deduplicated alert adds during an investigation is shown to the model before the hypothesis step (`late_evidence`, 2026-11-07).
- Devices are disabled, never deleted (DB-011 kept, 2026-11-04; contracts 0.2.1 wording).
- Two developers, direct pushes to `main` (`CLAUDE.md` §8.1, §11); a single developer from 2026-10-21 to 2026-11-03.
- `ocp_backend` is `NOLOGIN BYPASSRLS` in the migration; the login and password are set by hand per Supabase project. RLS stays on with no policies (`supabase/migrations/20261021190000_initial_schema.sql`).
- `audit_log` also refuses TRUNCATE (a statement trigger), besides UPDATE and DELETE.
- Migration CI: forward-only check against the previous head of `main`; names `<14 digits>_<snake>.sql`; Postgres 17 (`pgvector/pgvector:0.8.1-pg17`).
- Detector after a reset: nothing learnt for 150 s (ADR-24); a signal triggered before the reset recovers 3 min after it unless it still breaches (ADR-25); container restarts and non-zero exits before the end of those 150 s do not count (ADR-26).
- Earlier contract decisions (snake_case schemas, `msg` stream field, HMAC over the JSON as sent, hex keys, `proposal.fingerprint` formula, the 4 KB evidence cut) are unchanged; see `docs/checks/contract-review-*.md`.
