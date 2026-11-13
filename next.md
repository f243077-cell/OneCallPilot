# Next session — start here

> **For:** the next Claude Code session working for **Tanzeel** or **Usman** (two developers on `main` since 2026-11-04).
> **Read order:** `CLAUDE.md` (§11 = the rules for two developers on `main`), `context.md`, the newest `handoff.md` entry, then this file.
> **Rewrite your own developer's section** after every finished chunk of work, so a session that stops at a usage limit can be resumed from here alone (`CLAUDE.md` §11.4).

## Tanzeel — next (no LLM needed)
Done 2026-11-12: decisions sent to Usman (region, ADR-27, scenario 1, README; `docs/usman-work-plan.md` §2 D3 items 6–8, `handoff.md` 2026-11-12); README phone app paragraph. Earlier: MOB-016 app killed, ADR-26, Gate G3 criterion 2, B3.1, proxy 54/54, A3.8 storyboard, runbook review, DB-011 kept (contracts 0.2.1).
1. **Before the joint session:** wait for Usman's answer on (a) anything he needs from the Sydney `ocp-dev`, (b) whether he can open the Supabase org (if not, invite him by email from the org's Team settings).
2. **`ocp-bench` (can be done alone, any time):** delete the empty Sydney `ocp-bench`; create `ocp-bench` in `ap-south-1`; B0.1 settings (sign-ups off, asymmetric JWT key, `ocp` not in the Data API's exposed schemas, users Tanzeel, Usman and a test user). **Do not migrate it** (ADR-28: only at the freeze).
3. **`ocp-dev` switch, together with Usman in person:** both stacks `ocp down`; delete the Sydney `ocp-dev`; create `ocp-dev` in `ap-south-1` with the B0.1 settings; apply all migrations with the setup script (it records them in `supabase_migrations.schema_migrations`); `alter role ocp_backend with login password '…'`; put the session-pooler URL in `.env` as `DATABASE_URL` and the new `SUPABASE_URL`; the new `SUPABASE_ANON_KEY`, `OCP_TEST_*` and `SUPABASE_DB_ADMIN_URL` in `.env.local`; check DB-012 (an anon REST call fails). Type the `ocp_backend` password into Usman's `.env` yourself, in person. Usman's first `ocp up` embeds the runbooks (his key).
4. Rebuild the APK with the new `SUPABASE_URL` and anon key (README "Phone app"); both operators sign in; the phone re-registers its device.
5. Tick your box in `docs/checks/runbook-review.md`.
6. Then, with Usman: the manual half of the pilot (P4.1) on the Mumbai `ocp-dev`, after U8.
7. Still open: the DHCP reservation for 192.168.0.105; seating and whose fingerprint approves (D3 items 1 and 2).

## Usman — next (OpenRouter)
**The detailed list, with steps, "done when" and Phase 4, is `docs/usman-work-plan.md`.** Every OpenRouter call is Usman's (`CLAUDE.md` §11.6). The pilot runs on `ocp-dev`, `ocp-bench` only for counted runs (ADR-28).
State 2026-11-13: Gate G2 passed 7 of 8; model pinned (B2.13); **prompts v3 are the default** (scenario 1 decision); ADR-27 accepted; U5 done with v3 (7 of 8 recordings; scenario 5 does not record in v2 or v3, `docs/checks/gate-g2.md`); round-trip cut in the pool and evidence writes (`7ca25be`). Decisions D1–D4 in `handoff.md` 2026-11-12.
1. **Together, in person:** the switch to the new `ocp-dev` in `ap-south-1` (Tanzeel types the `ocp_backend` password into `.env`; the first `ocp up` embeds the runbooks with Usman's key). Both stacks down during the switch.
2. After the switch, idle laptop: `ocp up`, `ocp doctor`; time `select 1` through the pool into `latency.md`; then U4 + U7: two `ocp smoke` back to back, then 5 live runs for alert to proposal (target median ≤ 60 s; option D is allowed, option C needs Tanzeel). Then replace the README's latency limitation with the final numbers and region.
3. Scenario 5's tool recording, with the fixed recorder (`record.py 5`, `AGENT_ENABLED=false`), then its v3 model recording (`handoff.md` 2026-11-13 later).
4. P4.3: the clean-machine test on an idle laptop, again on the freeze candidate.
5. Top up OpenRouter before step 2; tick the runbook-review box.
6. Then U8 (procedure dry run, with Tanzeel) and Phase 4 (`docs/usman-work-plan.md` §4), the pilot on the Mumbai `ocp-dev`.
Live runs on this laptop: keep it idle. Docker: disk on `G:\DockerData`; on `E_ACCESSDENIED` for `ext4.vhdx`, quit Docker Desktop, `wsl --shutdown`, start again; a Store update of WSL stops Docker mid-run (2026-11-11).

## Together (Tanzeel's laptop and phone)
A3.3 live biometric approval, A3.7 all 8 scenarios from the phone, Gate G3, Phase 4.

## 0. Rules (short form of `CLAUDE.md` §11)
- Work on `main`; `git pull --rebase origin main` before you start and right before each push; commit and push directly (`git push origin main`). No branches, no PRs, no force-push, no tags unless Tanzeel asks.
- Run the checks of every touched package before each push (§11.2). Never push red. After a push, check CI on `main` (GitHub MCP `actions_list` → `list_workflow_runs`, branch `main`).
- **Commit identity (§11.5):** the session's own developer. Tanzeel: `git config user.name "Tanzeel Hussain"` and `git config user.email "f243077@cfd.nu.edu.pk"`; Usman: his own. Never add `Co-Authored-By`, `Claude-Session` or any AI attribution line.
- Never print or commit secrets. Things only one person can do by hand go under "Waiting on Tanzeel" or "Waiting on Usman" in `handoff.md`.

## 1. Start of the session (cloud container)
1. `git checkout main && git pull --rebase origin main`.
2. uv: CI pins **0.12.7**. If `uv --version` is older, use `/usr/local/bin/uv` (`pip install uv==0.12.7`).
3. Docker: if `docker info` fails, start the daemon: `(dockerd > /tmp/dockerd.log 2>&1 &)`. Docker Hub rate-limits this container (429), so pull through the mirror: `docker pull mirror.gcr.io/pgvector/pgvector:0.8.1-pg17`.
4. Test database: `docker run -d --name ocp-test-pg -e POSTGRES_PASSWORD=postgres -p 127.0.0.1:54329:5432 mirror.gcr.io/pgvector/pgvector:0.8.1-pg17`, then
   `cd backend && OCP_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54329/postgres uv run pytest -c pyproject.toml ../tests/db -q` (70 tests); the same for `../tests/integration` (10) and `../tests/security` (22, needs Docker; `docker tag mirror.gcr.io/library/redis:7.4.11-alpine redis:7.4.11-alpine` first). Unit tests: `uv run pytest -q tests`.

## 2. Next tasks, in order
**Since 2026-11-02 (local sessions on the laptop; cloud sessions are no longer used):** Supabase `ocp-dev` is migrated and `DATABASE_URL`, `SUPABASE_URL` are in `.env` (`SUPABASE_ANON_KEY`, `OCP_TEST_*`, `SUPABASE_DB_ADMIN_URL` in `.env.local`). The LLM is `LLM_PROVIDER=openrouter` (ADR-23).
**State 2026-11-03:** contracts 0.2.0 done (issue 12 and the escalation reason); ADR-24 detector fix; the stack runs on the laptop. **Blocked on OpenRouter credit** (HTTP 402): see `docs/checks/smoke.md`.
**State 2026-11-03 (evening):** push works on the real phone (`docs/checks/push.md`); scenario 8 option A done and all 8 re-recorded. Building the app for the phone: `flutter build apk --debug` with `DATA_SOURCE=live`, `API_BASE_URL=http://<laptop Wi-Fi IP>:8000`, `WS_URL=ws://<IP>:8000/ws/incidents`, `SUPABASE_URL` (`.env`), `SUPABASE_ANON_KEY` (`.env.local`); copy to the Desktop, Tanzeel installs it by hand (USB debugging is off). Afterwards `git checkout -- mobile/pubspec.lock` (local Flutter 3.47.7 rewrites it); lock changes are made with `D:lutter-sdks.44.2`.
**State 2026-11-03 (day):** B2.6 tool outputs recorded for all 8 scenarios (`tests/agent_regression/scenarios/recorded/`, 16 regression cases pass). ADR-25 detector fix (a reset starts the recovery clock) and a `chaos reset` fix (load off before the restarts) found by the recorder. Still blocked on credit for every live-model task.
0. After credit: `uv run --project tools/ocp ocp smoke` (the stack must have had a healthy warm-up; `ocp smoke` resets first).
A. (done 2026-11-03) **Restart the stack** (`ocp down`, `ocp up`): processes started while the Docker VM clock was 13 h ahead keep stale time-based caches (cs-lb's nginx DNS). Check that `cs-loadgen` reports `ok` again and Prometheus has `up` series.
B. **Proxy security test** (`bash runner/tests/security/run_proxy_test.sh`) and CI on the last push (the runner service and `required: false` fix).
B2. **`ocp smoke` live** (scenario 2, real model through OpenRouter); record the result in `docs/checks/`. The backend profile is in `ocp up` now; the runner service is in `runner.yml`.
   Test containers for the backend suites: `ocp-test-pg` (127.0.0.1:54329) and `ocp-test-redis` (127.0.0.1:63799); start them with `docker start ocp-test-pg ocp-test-redis`.
C. (tool recordings done 2026-11-03; re-record with `uv run python ../tests/agent_regression/record.py all` from `backend/`, about 50 min, stack up) **Recorded-model replay** with OpenRouter (`@pytest.mark.recorded`, needs credit), then B3.3/B3.4/B3.9.
   Open question for Tanzeel: scenario 8's planted text reaches the model only through INFO or ANY log queries (the ERROR line carries it in `exc_message`, which `query_logs` drops). See the recorded s8 file and `EXPECT_OVERRIDES` in `record.py`.
D. **Issue 12** (contracts 0.2.0): signed `proposal_kind` in `RunnerRequest`; the runner exempts rollbacks from its cooldown. Plus the escalation reason for a failed action without a rollback step.

Older list (cloud sessions):
Done: Phase 1 Stream B (B1.1–B1.8); B2.1–B2.5, B2.7–B2.12 (B2.12 with synthetic fixtures), FR-011, the worker entry point, B3.2, the detector following `monitor_settings`, B3.8 (Claude adapter), B3.6–B3.7 (benchmark harness), B3.5 (copilot metrics), backend Compose services (profile `backend`), README with Known limitations (S3.2 draft, SEC-018). Stream A: Phase 2 done (A2.1–A2.9); Phase 3 A3.1, A3.4, A3.5 done.
0. **CI first:** check all six workflows on the last pushed commit (`git log origin/main -1`); push any held local commits (`git log origin/main..main`) only once they are green. A runner job that dies pulling images (`toomanyrequests`) before any test ran may be re-run once.
1. **The backend profile runs here without Supabase** (done once; the recipe): a migrated database in the test pgvector container joined to `oncallpilot_default`; a throwaway `jwks` container (python http.server) serving `/auth/v1/.well-known/jwks.json` with an EC key you generate, and a token signed with it (`iss = http://jwks:8000/auth/v1`, `aud = authenticated`); `DATABASE_URL=postgresql://postgres:postgres@ocp-test-pg:5432/<db>`, `SUPABASE_URL=http://jwks:8000`, `LLM_PROVIDER=fake`; `docker compose --profile copilot --profile backend create` first (it creates the `default` network), then `up -d --wait`. Wait a full warm-up plus 60 s after a reset or a worker restart before `chaos inject`.
   Open follow-up from the first start: adding `backend` to `ocp up` once Supabase exists.
   **Shared folders are read by several suites:** `runbooks/` is checked by `backend/tests/test_runbook_corpus.py` *and* `chaos-shop/cli/tests/test_naming.py` (TB-010 word list); `contracts/` by every package. Run all of them before pushing a change there.
2. **Next phase work that needs no Supabase/LLM:** none left in Stream A except device work. Remaining Stream B: B2.6 recordings, B3.3/B3.4/B3.9 (need the Gemini key and the stack on Tanzeel's laptop). Phase 4 (pilot, freeze, benchmark) needs Tanzeel.
   **Running the stack here:** `cp .env.example .env`; build the six chaos-shop images and the runner image from scratch Dockerfile variants that add `COPY --from=ca ca.crt /ca.crt` + `ENV PIP_CERT=/ca.crt SSL_CERT_FILE=/ca.crt UV_NATIVE_TLS=1` to the `deps` stage (`--build-context ca=<dir with /root/.ccr/ca-bundle.crt>`, `--network host`, `--build-arg HTTPS_PROXY`); pull `nginx:1.30.5-alpine` and `postgres:17.11-alpine` via `mirror.gcr.io/library/…` and re-tag; `compose.override.yaml` with `DISABLE_IPV6: "1"` for both socket proxies (excluded from git); `uv run --project tools/ocp ocp up --no-build`.
   Live checks of the app's data layer: start a local backend-api (scratch recipe: a fresh migrated DB on the test Postgres, `backend/tests/keys.SigningKey` for a token, `Approvals(...)` for the approval routes, uvicorn on 127.0.0.1), seed with `python -m oncallpilot.seed … --new-ids`, then `flutter test test/live` with `OCP_LIVE_API_URL`, `OCP_LIVE_WS_URL`, `OCP_LIVE_TOKEN`, `OCP_LIVE_SEED`. Never `pkill -f` a pattern that appears in your own command line (it kills the shell).
   Flutter in the cloud container: `curl -sSL https://storage.googleapis.com/flutter_infra_release/releases/stable/linux/flutter_linux_3.44.2-stable.tar.xz | tar xJ -C /opt/flutter-sdk` (1.5 GB), `git config --global --add safe.directory /opt/flutter-sdk/flutter`, `PATH=/opt/flutter-sdk/flutter/bin:$PATH`; CI also checks `dart run build_runner build` leaves `lib/` unchanged.
2. **B2.6 / TEST-002 recorded half** (needs the live testbed and the Gemini key, so Tanzeel's laptop): record real tool outputs per scenario into `tests/agent_regression/scenarios/*.yaml` (same format as the synthetic ones) and add the `@pytest.mark.recorded` real-model replay.
3. **B3.9** procedure dry run (2 scenarios × 2 modes) and **B3.3/B3.4** need the live stack, Supabase and a real LLM key — Tanzeel's laptop.
4. **Start the `backend` profile for real** once Supabase exists (`docker compose --profile backend up -d`), fix whatever the first start shows, then add `backend` to `PROFILES` in `tools/ocp/ocp_cli/stack.py` so `ocp up` starts it, and run `ocp smoke`.
5. Raise with Tanzeel (do not edit `contracts/` unasked): escalation reason for a failed action without a rollback step (chunk 7); device DELETE vs DB-011 (chunk 8).
- **Run the Python suites one at a time** (`../tests/integration`, `../tests/agent_regression`, `../tests/security`, `../tests/db`): collected together, their `conftest` modules collide (the integration tests import helpers `from conftest`). CI runs them as separate steps.
- **CI notes:** the runner workflow takes ~15 min and is cancelled by every new push to `main`, so batch pushes. Before pushing a compose change, also validate it with Compose v2 (GitHub's runners): `curl -sSL -o /tmp/docker-compose-v2 https://github.com/docker/compose/releases/download/v2.38.2/docker-compose-linux-x86_64 && chmod +x /tmp/docker-compose-v2`, then `/tmp/docker-compose-v2 --env-file .env.example --profile testbed --profile testbed-slots --profile obs --profile copilot --profile runner --profile tools config --quiet`.
- **Cloud container notes:** socket-proxy images need `DISABLE_IPV6=1` here (no IPv6); use a local override file, never commit it. Pull Docker Hub images through `mirror.gcr.io/...` and re-tag them; `lscr.io` is blocked (403), but `mirror.gcr.io/linuxserver/socket-proxy:3.4.6-r0-ls101` works. Building images needs the proxy CA (see the B1.4 commit and `docs`: a throwaway Dockerfile variant in the scratchpad).

## 3. Notes carried over for Stream B tasks
- **B1.5:** `honor_labels: true`; name `chaos_net` plainly (`chaos_net: {}`), never `external: true`; loggers are `shop.*`.
- **B1.7:** scenario 5 is detected by `p95_latency`.
- **B1.8:** `ERROR` should match `ERROR|CRITICAL`.
- **B2.2:** `restart_count` instead of `OOMKilled`; ledger volume `deploy_ledger`, read-only at `/ledger`, `LEDGER_PATH=/ledger/deploys.jsonl`, files owned by uid 10001.
- **B2.10:** name the audit event for a dropped runner result.
