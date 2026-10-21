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

## 2. Next tasks, in order
Done: Phase 1 Stream B (B1.1–B1.8); B2.1–B2.5, B2.7–B2.12 (B2.12 with synthetic fixtures), FR-011, the worker entry point, B3.2, the detector following `monitor_settings`, B3.8 (Claude adapter), B3.6–B3.7 (benchmark harness), B3.5 (copilot metrics). Stream A is at A2.5 (earlier work).
0. **CI first:** check all six workflows on the last pushed commit (`git log origin/main -1`); push any held local commits (`git log origin/main..main`) only once they are green. A runner job that dies pulling images (`toomanyrequests`) before any test ran may be re-run once.
1. **B2.6 / TEST-002 recorded half** (needs the live testbed and the Gemini key, so Tanzeel's laptop): record real tool outputs per scenario into `tests/agent_regression/scenarios/*.yaml` (same format as the synthetic ones) and add the `@pytest.mark.recorded` real-model replay.
2. **B3.9** procedure dry run (2 scenarios × 2 modes) and **B3.3/B3.4** need the live stack, Supabase and a real LLM key — Tanzeel's laptop.
3. **backend-api / backend-worker in `copilot.yml`** once Supabase exists (needs `DATABASE_URL`); worker gets `RUNNER_LINK_KEY` but never `ACTION_SIGNING_KEY` (SEC-002 compose test), the ledger read-only at `/ledger`, the FCM file read-only; api gets `LOKI_URL` on obs_net.
4. Raise with Tanzeel (do not edit `contracts/` unasked): escalation reason for a failed action without a rollback step (chunk 7); device DELETE vs DB-011 (chunk 8).
- **Run the Python suites one at a time** (`../tests/integration`, `../tests/agent_regression`, `../tests/security`, `../tests/db`): collected together, their `conftest` modules collide (the integration tests import helpers `from conftest`). CI runs them as separate steps.
- **CI notes:** the runner workflow takes ~15 min and is cancelled by every new push to `main`, so batch pushes. Before pushing a compose change, also validate it with Compose v2 (GitHub's runners): `curl -sSL -o /tmp/docker-compose-v2 https://github.com/docker/compose/releases/download/v2.38.2/docker-compose-linux-x86_64 && chmod +x /tmp/docker-compose-v2`, then `/tmp/docker-compose-v2 --env-file .env.example --profile testbed --profile testbed-slots --profile obs --profile copilot --profile runner --profile tools config --quiet`.
- **Cloud container notes:** socket-proxy images need `DISABLE_IPV6=1` here (no IPv6); use a local override file, never commit it. Pull Docker Hub images through `mirror.gcr.io/...` and re-tag them; `lscr.io` is blocked (403), but `mirror.gcr.io/linuxserver/socket-proxy:3.4.6-r0-ls101` works. Building images needs the proxy CA (see the B1.4 commit and `docs`: a throwaway Dockerfile variant in the scratchpad).

## 3. Notes carried over for Stream B tasks
- **B1.5:** `honor_labels: true`; name `chaos_net` plainly (`chaos_net: {}`), never `external: true`; loggers are `shop.*`.
- **B1.7:** scenario 5 is detected by `p95_latency`.
- **B1.8:** `ERROR` should match `ERROR|CRITICAL`.
- **B2.2:** `restart_count` instead of `OOMKilled`; ledger volume `deploy_ledger`, read-only at `/ledger`, `LEDGER_PATH=/ledger/deploys.jsonl`, files owned by uid 10001.
- **B2.10:** name the audit event for a dropped runner result.
