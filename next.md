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
Done: B1.1 migrations, B1.2 API skeleton, B1.3 copilot-redis + ACL + `/ws/incidents`, B1.4 `ocp seed-incident`, B1.6 `POST /ingest/alert`, B1.7 detector engine + loop (pure; no real sources yet).
1. **B1.5 observability compose** in `infrastructure/compose/observability.yml` (profile `obs`): Prometheus (DNS service discovery on `chaos_net`, `honor_labels: true`), Loki, Alloy reading container logs through socket-proxy-ro (`discovery.docker` + `loki.source.docker`, label `oncallpilot.logs=true`), Grafana with a provisioned dashboard; pinned images, healthchecks, ports bound to 127.0.0.1 only (3000, 9090, 3100). Read `contracts/telemetry.md` (C7) for metric names, labels and log fields. `ocp up` (and CI's runner-live, which runs it) must stay green: watch memory and image pulls (Docker Hub rate limits were seen in CI).
2. **B1.8** `query_logs` and `query_metrics` (AI-005, AI-006): fixed LogQL template and the 12 PromQL templates of §6.3, enum parameters, limits, against a fake HTTP server in tests. The same Prometheus/Loki clients become the detector's `SampleSource` (`oncallpilot/detector/loop.py`).
3. **Worker entry point** (`oncallpilot/worker/`): runs the detector loop (DETECTOR_INTERVAL_SECONDS) and, later, the agent consumer; auto-resolver for FR-011 from `DetectorLoop.on_recovered`.
4. backend-api/worker services in `copilot.yml` once Supabase exists (Tanzeel).
5. Then Phase 2 Stream B (B2.1 agent state machine + FakeProvider …) per `docs/phases.md` §5.
- **CI note:** the runner workflow takes ~15 min and is cancelled by every new push to `main`. Batch pushes, or wait for it, so it completes at least once per chunk.

## 3. Notes carried over for Stream B tasks
- **B1.5:** `honor_labels: true`; name `chaos_net` plainly (`chaos_net: {}`), never `external: true`; loggers are `shop.*`.
- **B1.7:** scenario 5 is detected by `p95_latency`.
- **B1.8:** `ERROR` should match `ERROR|CRITICAL`.
- **B2.2:** `restart_count` instead of `OOMKilled`; ledger volume `deploy_ledger`, read-only at `/ledger`, `LEDGER_PATH=/ledger/deploys.jsonl`, files owned by uid 10001.
- **B2.10:** name the audit event for a dropped runner result.
