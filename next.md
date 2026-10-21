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
Done: Phase 1 Stream B (B1.1–B1.8); B2.1–B2.5, B2.7. Stream A is at A2.5 (earlier work).
1. **B2.8** proposal validation and dry run (architecture §5.6, §7.2, §7.5; AI-018, AI-019, FR-010): a real `Proposer` for `agent/effects.py` replacing `UnavailableProposer`. Validate against `contracts/actions.yaml` (enabled actions only; `run_migration_rollback` stays disabled), parameter enums built at runtime from `runner/targets.yaml` and the deploy ledger, risk tier / approval requirement / rollback step copied from the catalogue; `audit(proposal.rejected_by_validator)` on failure. Dry run: signed `dry_run` request on `ocp:runner:requests` with `RUNNER_LINK_KEY` (C5: `contracts/python/oncallpilot_contracts/runner.py`, `signing.py`, vectors in `contracts/fixtures/signing/`), wait ≤ 15 s for the result with the same `request_id`, retry once. Then insert the pending proposal (fingerprint per `contracts/approval-protocol.md` §2, `expires_at = now + 900 s`), incident → `awaiting_approval`, `proposal.created`. Read the runner's side (`runner/oncallpilot_runner/`) to match message shapes. Test with a `FakeRunnerConsumer`.
2. **B2.9** challenge / approve / reject endpoints (C8 `contracts/approval-protocol.md`; API-008…API-010, SEC-001…SEC-003, SEC-012): checks in order, idempotency, rate limit and cooldown, signed `execute` with `ACTION_SIGNING_KEY` (backend-api only).
3. **Worker entry point** `oncallpilot/worker/__main__.py`: settings, detector loop with real sources, agent-job consumer wiring `Investigation(GeminiProvider | FakeProvider, DbEffects, FilePrompts)`, runbook ingestion at start-up; then backend-api/worker services in `copilot.yml` once Supabase exists.
4. B2.10 (runner-result consumer, verification, rollback proposals, sweeper), B2.11 (FCM sender, device endpoints), B2.12 (agent regression suite).
- **Worker entry point** (`oncallpilot/worker/`) with the detector loop and real sample sources (Prometheus/Loki/socket-proxy-ro) can be built alongside B2.4.
- **CI notes:** the runner workflow takes ~15 min and is cancelled by every new push to `main`, so batch pushes. Before pushing a compose change, also validate it with Compose v2 (GitHub's runners): `curl -sSL -o /tmp/docker-compose-v2 https://github.com/docker/compose/releases/download/v2.38.2/docker-compose-linux-x86_64 && chmod +x /tmp/docker-compose-v2`, then `/tmp/docker-compose-v2 --env-file .env.example --profile testbed --profile testbed-slots --profile obs --profile copilot --profile runner --profile tools config --quiet`.
- **Cloud container notes:** socket-proxy images need `DISABLE_IPV6=1` here (no IPv6); use a local override file, never commit it. Pull Docker Hub images through `mirror.gcr.io/...` and re-tag them; `lscr.io` is blocked (403), but `mirror.gcr.io/linuxserver/socket-proxy:3.4.6-r0-ls101` works. Building images needs the proxy CA (see the B1.4 commit and `docs`: a throwaway Dockerfile variant in the scratchpad).

## 3. Notes carried over for Stream B tasks
- **B1.5:** `honor_labels: true`; name `chaos_net` plainly (`chaos_net: {}`), never `external: true`; loggers are `shop.*`.
- **B1.7:** scenario 5 is detected by `p95_latency`.
- **B1.8:** `ERROR` should match `ERROR|CRITICAL`.
- **B2.2:** `restart_count` instead of `OOMKilled`; ledger volume `deploy_ledger`, read-only at `/ledger`, `LEDGER_PATH=/ledger/deploys.jsonl`, files owned by uid 10001.
- **B2.10:** name the audit event for a dropped runner result.
