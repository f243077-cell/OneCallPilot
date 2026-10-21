# Next session — start here

> **For:** the next Claude Code session (cloud or local) working for **Tanzeel**, the only developer since 2026-10-21.
> **Read order:** `CLAUDE.md` (§11 = the single-developer rules), `context.md`, the newest `handoff.md` entry, then this file.
> **Rewrite this file** after every finished chunk of work, so a session that stops at a usage limit can be resumed from here alone.

## 0. Rules (short form of `CLAUDE.md` §11)
- Work on `main`; commit and push directly (`git push origin main`). No branches, no PRs, no force-push, no tags unless Tanzeel asks.
- Run the checks of every touched package before each push (§11.2). Never push red. After a push, check CI on `main` (GitHub MCP `actions_list` → `list_workflow_runs`, branch `main`).
- Never print or commit secrets. Things only Tanzeel can do by hand go under "Waiting on Tanzeel" in `handoff.md`.

## 1. Start of the session (cloud container)
1. `git checkout main && git pull --ff-only origin main`.
2. uv: CI pins **0.12.7**. If `uv --version` is older, use `/usr/local/bin/uv` (`pip install uv==0.12.7`).
3. Docker: if `docker info` fails, start the daemon: `(dockerd > /tmp/dockerd.log 2>&1 &)`. Docker Hub rate-limits this container (429), so pull through the mirror: `docker pull mirror.gcr.io/pgvector/pgvector:0.8.1-pg17`.
4. Test database: `docker run -d --name ocp-test-pg -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:54329:5432 mirror.gcr.io/pgvector/pgvector:0.8.1-pg17`, then
   `cd backend && OCP_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54329/postgres uv run pytest -c pyproject.toml ../tests/db -q` (70 tests).

## 2. Next tasks, in order (Phase 1, Stream B — Stream A is already into Phase 2)
1. **B1.2** backend-api skeleton: settings (pydantic-settings), JWKS auth (PyJWT, `SUPABASE_URL` → JWKS URL and `iss`), the error envelope in `backend/oncallpilot/api/errors.py` (architecture §9.8), `/healthz`, `GET /incidents`, `GET /incidents/{id}` (repositories in `backend/oncallpilot/db/`). Tests with a local RSA key and a fake JWKS; DB tests against the pgvector container. Replace the placeholder `backend.yml` with real steps (uv sync --locked, ruff, format, mypy, pytest with a pgvector service). Requirements: API-001, API-002, API-004, API-005, SEC-010.
2. **B1.3** copilot-redis with ACL users (`infrastructure/redis/users.acl`), `ocp:events` publisher, `/ws/incidents` (auth, hello, ping, resume, resync). API-014…API-017, SEC-004.
3. **B1.4** `ocp seed-incident <timeline>` in `tools/ocp`.
4. **B1.6** `POST /ingest/alert`; **B1.7** detector v1 (unit tests on synthetic series); **B1.5** observability compose; **B1.8** first tools.
5. Then Phase 2 Stream B (B2.1 …) per `docs/phases.md` §5.

## 3. Notes carried over for Stream B tasks
- **B1.5:** `honor_labels: true`; name `chaos_net` plainly (`chaos_net: {}`), never `external: true`; loggers are `shop.*`.
- **B1.7:** scenario 5 is detected by `p95_latency`.
- **B1.8:** `ERROR` should match `ERROR|CRITICAL`.
- **B2.2:** `restart_count` instead of `OOMKilled`; ledger volume `deploy_ledger`, read-only at `/ledger`, `LEDGER_PATH=/ledger/deploys.jsonl`, files owned by uid 10001.
- **B2.10:** name the audit event for a dropped runner result.
