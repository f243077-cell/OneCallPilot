# Next session — start here

> **For:** the next Claude Code session (cloud or local) working for **Tanzeel**, the only developer since 2026-10-21.
> **Read order:** `CLAUDE.md` (§11 = the single-developer rules), `context.md`, the newest `handoff.md` entry, then this file.
> **Rewrite this file** after every finished chunk of work, so a session that stops at a usage limit can be resumed from here alone.

## 0. Rules (short form of `CLAUDE.md` §11)
- Work on `main`; commit and push directly (`git push origin main`). No branches, no PRs, no force-push, no tags unless Tanzeel asks.
- Run the checks of every touched package before each push (§11.2). Never push red. After a push, check CI on `main` (GitHub MCP `actions_list` → `list_workflow_runs`, branch `main`).
- **Commit identity (§11.5):** `git config user.name "Tanzeel Hussain"` and `git config user.email "f243077@cfd.nu.edu.pk"` before the first commit; never add `Co-Authored-By`, `Claude-Session` or any AI attribution line.
- Never print or commit secrets. Things only Tanzeel can do by hand go under "Waiting on Tanzeel" in `handoff.md`.

## 1. Start of the session (cloud container)
1. `git checkout main && git pull --ff-only origin main`.
2. uv: CI pins **0.12.7**. If `uv --version` is older, use `/usr/local/bin/uv` (`pip install uv==0.12.7`).
3. Docker: if `docker info` fails, start the daemon: `(dockerd > /tmp/dockerd.log 2>&1 &)`. Docker Hub rate-limits this container (429), so pull through the mirror: `docker pull mirror.gcr.io/pgvector/pgvector:0.8.1-pg17`.
4. Test database: `docker run -d --name ocp-test-pg -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:54329:5432 mirror.gcr.io/pgvector/pgvector:0.8.1-pg17`, then
   `cd backend && OCP_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54329/postgres uv run pytest -c pyproject.toml ../tests/db -q` (70 tests); the same for `../tests/integration` (10) and `../tests/security` (22, needs Docker; `docker tag mirror.gcr.io/library/redis:7.4.11-alpine redis:7.4.11-alpine` first). Unit tests: `uv run pytest -q tests`.

## 2. Next tasks, in order (Phase 1, Stream B — Stream A is already into Phase 2)
Done so far: B1.1 (migrations), B1.2 (API skeleton), B1.3 part 1 (copilot-redis + ACL).
1. **B1.3 part 2:** `oncallpilot/events.py` publisher (`XADD ocp:events MAXLEN ~10000`, the C3 envelope from `contracts/python/oncallpilot_contracts/ws.py`), and `/ws/incidents` in `oncallpilot/api/routers/ws.py`: `auth` within 5 s (else close 4408), invalid token → 4401, re-`auth` on refresh, close 4401 on expiry; `hello`; `resume` → replay or `resync_required`; `ping` every 15 s, close after 2 missed pongs. Tests with a fake clock and a real Redis (throwaway container like `tests/security/test_redis_acl.py`, or `fakeredis` if added). API-014…API-017. Read `contracts/ws-protocol.md` first.
2. **B1.4** `ocp seed-incident <timeline>`: `oncallpilot.db.seed.write_incident_detail` already writes `final`; add a CLI entry (`python -m oncallpilot.db.seed`) that also publishes the timeline's events, and an `ocp seed-incident` command in `tools/ocp` that runs it.
3. **B1.6** `POST /ingest/alert` (API-003, FR-004…FR-007); **B1.7** detector v1; **B1.5** observability compose; **B1.8** first tools.
4. Then Phase 2 Stream B (B2.1 …) per `docs/phases.md` §5.
- **Not yet in compose:** backend-api/worker (they need a real database; adding them before Supabase exists would break `ocp up`), and the runner service in `runner.yml` (copilot-redis now exists; adding it is Stream A follow-up, see the comment in `runner.yml` and `runner/tests/runner_service.yml`).

## 3. Notes carried over for Stream B tasks
- **B1.5:** `honor_labels: true`; name `chaos_net` plainly (`chaos_net: {}`), never `external: true`; loggers are `shop.*`.
- **B1.7:** scenario 5 is detected by `p95_latency`.
- **B1.8:** `ERROR` should match `ERROR|CRITICAL`.
- **B2.2:** `restart_count` instead of `OOMKilled`; ledger volume `deploy_ledger`, read-only at `/ledger`, `LEDGER_PATH=/ledger/deploys.jsonl`, files owned by uid 10001.
- **B2.10:** name the audit event for a dropped runner result.
