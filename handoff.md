# Handoff — session log

> One dated entry per session (or finished chunk), **newest first**, with **Done** (with commits), **Next** (in order), and **Waiting on Tanzeel**. Old entries are never edited (`CLAUDE.md` §11.4). Entries before 2026-10-21 were written for Usman's sessions; their branch rules are obsolete.

## 2026-10-21 — Cloud session 1, chunk 13: copilot metrics

### Done
- **B3.5** (`02cdc39`): `oncallpilot/metrics.py` (prometheus-client, **new dependency**): agent duration (histogram + per-incident gauge), tool calls by outcome, LLM latency, LLM errors, escalations by reason, recorded by `DbEffects`; backend-worker serves them on `WORKER_METRICS_PORT` (9464, never published). Prometheus job `copilot` (DNS discovery of `backend-worker`; empty until the worker is in Compose) — `promtool check config` passes. Grafana dashboard **Copilot internals** (5 panels; loads in Grafana 12.1.1).
- Totals: 308 unit, 120 integration, 8 regression, 113 security, 31 bench tests passing locally.

### Decisions to confirm (Tanzeel)
- backend-worker must join `obs_net` when it is added to `copilot.yml`, so Prometheus can scrape it.
- The per-incident duration gauge carries `incident_id` as a label (a few dozen incidents per benchmark; fine at this scale, not for production).
- backend-api exposes no `/metrics` of its own (it is the only service on 0.0.0.0, so a metrics endpoint there would be on the LAN).

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged (chunks 7–12).

## 2026-10-21 — Cloud session 1, chunk 12: benchmark harness

### Done
- **B3.6** (`978fcfc`, `481c3e7`): `benchmark/` is its own uv project with the `bench` CLI. `scenarios.yaml` (BENCH-001), `bench plan --seed --people` (48 runs, reproducible, operators balanced, observer ≠ operator), `schema/runs.csv.md` + `bench check` (BENCH-007), `bench run RUN_ID --plan …` (the whole BENCH-004 procedure), `bench mark diagnosis|decision|note|action-correct`, and `PROCEDURE.md` (roles, per-run steps, tool lists per mode).
- **B3.7** (`91b0e3c`): `bench audit-safety` (BENCH-010), `bench audit-evidence --labeler` (BENCH-009), `bench report` → `results/report.md` + `results/charts/*.png`, byte-identical on regeneration (BENCH-011, BENCH-013).
- New CI job `bench` in the backend workflow (ruff, mypy strict, 31 tests). Backend CI green on `978fcfc`.
- New dependencies (benchmark only): httpx, psycopg[binary], matplotlib, pyyaml, pydantic.

### Decisions to confirm (Tanzeel)
- **`bench` reads Postgres directly** (`DATABASE_URL` on the integration host, read-only session): the API does not expose `benchmark_run_id` or `detector_config_version`, which BENCH-004/007 need. It uses the API only for the BENCH-004 leftover cleanup (as the test user).
- **copilot-redis from the host:** `bench` clears limits/cooldowns and sets `ocp:bench:current_run` through `docker compose exec -T -e REDISCLI_AUTH copilot-redis redis-cli --user backend …`; the password goes in the environment, never on a command line. The mode check reads `AGENT_ENABLED` from `docker compose exec backend-api printenv` (needs backend-api in compose, next.md §2).
- **Judging:** `diagnosis_correct` defaults to a keyword/category match on the marked text (BENCH-008 wants the observer's judgement; edit the marks, never the CSV, if you disagree); `action_correct` in manual mode comes from `bench mark action-correct`. "Unsafe" in a row = any executed action other than the expected one (a rollback of the expected action is allowed); the audit adds the BENCH-010 checks.
- Time-to-diagnosis/recovery are measured from `t_alert` (project-requirements §8.2); a run without a correct diagnosis or recovery within 20 min is censored at 20 min.
- `bench run` ends a copilot run on escalation only for scenarios 7 and 8; otherwise it keeps waiting for a manual fix until the timeout.

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged (chunks 7–11). A second person is needed for the benchmark (operator ≠ observer, BENCH-003).

## 2026-10-21 — Cloud session 1, chunk 11: Claude adapter

### Done
- **History fix** (`cd4ed11`): the investigation keeps each structured call's prompt, any invalid reply with its repair request, and the valid reply in the conversation, exactly as sent; `Msg`/`LLMTurn`/`Structured` carry the provider's raw content. Before, the prompt was dropped and only a re-serialised reply was kept.
- **B3.8 Claude adapter** (`6da32a1`): `agent/providers/anthropic.py` on the official `anthropic` SDK (**new dependency, anthropic 2.0.0**, pulls `httpx2`). `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` (+ `LLM_MODEL=claude-opus-5-5`); embeddings stay on Gemini (ADR-14). 7 adapter tests against a mocked API; AI-021 import-lint test.
- CI: the runner re-run of `76877d1` got past the registry rate limit (`runner`, `runner-proxy` green; `runner-live` running at the time of writing).

### Decisions to confirm (Tanzeel)
- **No "temperature 0" on Claude:** current Claude models always think and reject sampling parameters, so the Claude adapter cannot be deterministic the way architecture §6.2/§6.7 assumes; it sets `effort: low` to fit the 90 s budget. If the benchmark falls back to Claude (ADR-13), record this in the results.
- **No constrained decoding for structured replies on Claude:** the API's schema mode cannot express the free-form `params` objects of `ProposalOut`/`DisproofPlanOut` (they would always come back `{}`), so the schema goes in the prompt and replies are validated with Pydantic (one repair, as for Gemini).
- **No server-side model fallback on refusals:** Anthropic recommends enabling it for these models, but a silent switch to another model would break ADR-13's "exactly one fixed model" rule for the benchmark. A refusal counts as an invalid structured reply (→ repair → `llm_output_invalid`). Turn it on later only if you accept that trade-off.
- Default model for the Claude path: `claude-opus-5-5` (ADR-13 pins the exact ID only if the fallback is used).

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged (chunks 7–10), plus an `ANTHROPIC_API_KEY` only if the Claude fallback is ever needed.

## 2026-10-21 — Cloud session 1, chunk 10: detector follows settings, regression suite

### Done
- **Detector follows `monitor_settings`** (`56e384c`): each cycle the worker's detector re-reads `monitor_settings`; on a new version it rebuilds its rules from `detector.yaml` + threshold overrides and evaluates only the monitored services (baselines kept; an unreadable or invalid version keeps the last good rules).
- **B2.12** (`e96549c`): `tests/agent_regression/` replays all **8 scenarios** through the real pipeline (ingest, `DbEffects`, real tools over mocked Loki/Prometheus/socket-proxy-ro, redaction, citations, reflection, prompts v1, catalogue validation, signed dry run via the `FakeRunnerConsumer`). Asserts terminal state, action/params or escalation reason, budgets, citations, suspicious flags, and that a planted secret never reaches evidence. New CI step "Agent regression suite (TEST-002)"; ruff/mypy cover the folder.
- Expected outcomes encoded (architecture §11.3): 1 → `restart_service(worker)`, 2 → `rollback_deploy(api, 1.4.0)`, 3 → `restart_service(api)`, 4 → `restart_service(redis)`, 5 → `scale_service(api, 3)`, 6 → `rollback_deploy(worker, 2.1.0)`, 7 → escalate `no_catalogue_action_fits`, 8 → escalate `low_confidence` with the injected log evidence flagged.

### Decisions to confirm (Tanzeel)
- The scenario tool outputs are **synthetic**; B2.6 must replace them with outputs recorded from the live testbed (you inject, the worker records). The second half of TEST-002 (recorded real-model replies, `@pytest.mark.recorded`) needs the Gemini key.
- Scenario 4's alert can only be the `restarts` signal with value 0 (the FR-006 sev1 "no running container" contract gap from chunk 3 still applies).

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged (chunks 7–9).

## 2026-10-21 — Cloud session 1, chunk 9: operations endpoints (B3.2)

### Done
- **B3.2** (`cee0177`): `POST /incidents/{id}/resolve` (manual, safe to repeat, `INCIDENT_BUSY` while an execution is queued/running; shares `oncallpilot/resolution.py` with the auto-resolver), `GET /incidents/{id}/log-tail` (redacted, oldest first, ≤ 10 min back, 1 request / 2 s / user, in-process limiter), `GET /audit` (keyset pages), `GET/PUT /settings` (defaults until first save, `VERSION_CONFLICT`, `BENCHMARK_LOCKED`, unknown thresholds refused). 119 integration tests pass.
- CI on `76877d1`: the runner workflow's `runner-live` and `runner-proxy` jobs failed pulling the socket-proxy images (`toomanyrequests` from the registry) before any test ran; the commit does not touch the runner. Failed jobs re-run once (run 39675271852); pushes held until it finishes.

### Decisions to confirm (Tanzeel)
- `detector_config_version` changes only when thresholds change (it hashes the detector file plus the threshold overrides). FR-023/ADR-20 say every settings update changes it; a change to `services` or `notify` currently does not. Small follow-up if you want it literal.
- The running detector in backend-worker reads `detector.yaml` at start and does not yet apply threshold overrides or the monitored-services list from `monitor_settings` live. Incidents are stamped correctly, but detection uses the file until the worker restarts with code that reloads settings (follow-up task in `next.md`).
- `/log-tail` returns `500 INTERNAL` ("logs are unavailable") when Loki is down; OpenAPI lists no 503.

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged (chunks 7–8).

## 2026-10-21 — Cloud session 1, chunk 8: auto-resolve and pushes

### Done
- Pushed chunk 7 after all six workflows were green on `d58c0b7`.
- **FR-011 auto-resolve** (`d085790`): `AutoResolver` in `worker/loops.py`; an open incident with no execution in progress whose triggering signals stay within the verification bounds for 3 minutes → `resolved` (`auto_recovered`); a pending proposal is closed as `expired`. "Normal since" is in memory (a worker restart only delays it); stale samples never count.
- **B2.11** (`76877d1`): `oncallpilot/push/` (FCM HTTP v1 over httpx with a service-account RS256 assertion signed by PyJWT, no new dependency; `FakePushTransport`; `Notifier` hooked into `EventStream`), `POST /devices`, `DELETE /devices/{id}`. A security test checks that the worker process loads neither the API nor the approve/execute code.
- Totals: 297 unit, 111 integration, 113 security, 70 db tests passing locally.

### Decisions to confirm (Tanzeel)
- **Docs disagree:** FR-018 says unregistered tokens are *deleted* and §10.5 says the device row is deleted on sign-out, but DB-011 gives `ocp_backend` no DELETE on any table. I kept DB-011: such devices get `push_enabled = false` (and `DELETE /devices/{id}` does the same, owner-only, 204, safe to repeat). If you prefer real deletes, a forward migration can `grant delete on ocp.devices to ocp_backend` and DB-011/test_privileges change with it.
- C9 says pushes go "through `firebase-admin`"; I used the same FCM HTTP v1 API through httpx + PyJWT to avoid a large dependency. The message shape is unchanged.
- `POST /devices` with a token already registered by another user moves the device to the caller (the same phone signed in as someone else).

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged (see chunk 7), plus: a Firebase service-account JSON in `infrastructure/secrets/` for real pushes (A0.1).

## 2026-10-21 — Cloud session 1, chunk 7: approvals, worker entry point, execution outcomes

### Done
- **B2.9** (`e511e35`): `POST /proposals/{id}/challenge|approve|reject` with the C8 checks in order (`oncallpilot/approvals/`: single-use `GETDEL` challenges with only the nonce hash stored, Idempotency-Key store with replay/conflict and a `SET NX` claim so a concurrent double tap yields one execution, rate limit 3/30 min and 120 s cooldown with `retry_after`, rollbacks exempt from cooldown). Approval + execution + statuses + SEC-017 audit snapshot in one transaction, then the `execute` request signed with `ACTION_SIGNING_KEY`, built only in `approvals/execute.py` (SEC-001 code-search test in `tests/security/`). Redis down → refused. CI now also enforces approvals coverage ≥ 90 % (91 %).
- **Detector live source** (`85da8f6`): Prometheus instant queries (same templates as `query_metrics`), Loki `Traceback` counts by `exc_type`, container states through socket-proxy-ro (GET only).
- **Worker entry point** (`d58c0b7`): `python -m oncallpilot.worker` runs the agent-job consumer (Investigation + DbEffects + RunnerProposer), the detector loop, runbook ingestion at start-up; SIGTERM drains. The backend image now contains `catalogue/actions.yaml`, `catalogue/targets.yaml` and `runbooks/` (build contexts `targets=runner`, `runbooks=runbooks`; CI build line and `copilot.yml` updated; Compose v2 config check passed). Tried by hand against local Postgres/Redis with every source down: starts, logs, stops cleanly.
- **Refactor** (`d768a14`): `oncallpilot/proposal_store.py` stores every kind of pending proposal.
- **B2.10** (`b904900`): `worker/outcomes.py` + `worker/loops.py`: runner-result consumer (group `backend`, signatures verified), the FR-024 status table, rollback proposals (FR-015) from the runner's `captured` values, drift → supersede + fresh dry run + new proposal (FR-017), DB-driven signal verifier (FR-014/FR-016), sweeper for expiry (FR-013) and runner timeouts (FR-024). 12 integration tests with signed fake results (`tests/integration/test_outcomes.py`); the approval test fixture moved to `tests/integration/approval_env.py`.
- Totals: 291 unit, 104 integration, 112 security tests passing locally; agent coverage 98 %, approvals 91 %.

### Decisions to confirm (Tanzeel)
- **Contract gap:** a failed action **without** a rollback step (restart_service, clear_cache) should be `escalated` (FR-015), but C1's `EscalationReason` has no fitting value. For now the incident stays `action_failed` (a human resolves it). Proposed contract change (minor bump): add `action_failed` (or `no_rollback_available`) to `EscalationReason`, then escalate with it.
- **Signal verification bounds** (`detector/verify.py`): a triggering signal counts as normal when it is under the rule's absolute floors (stricter than the detector, and independent of in-memory baselines so a restart cannot break it). No data is never normal.
- **Restarts signal:** only containers that are running or restarting count, so a slot stopped by a rollback no longer blocks verification.
- **Approve after the sweeper expired a proposal** returns `PROPOSAL_EXPIRED` (FR-013), not `PROPOSAL_NOT_PENDING`.
- An unregistered `device_id` on approve is recorded in the audit snapshot but not linked in `approvals.device_id` (FK to `devices`, which B2.11 fills).
- A dropped runner result (bad signature, malformed) is logged as a WARNING; no new audit event name was invented.
- New env vars (documented in `.env.example`): `INGEST_URL`, `PROMETHEUS_URL`, `LOKI_URL`, `CATALOGUE_PATH`, `TARGETS_PATH`, `RUNBOOKS_DIR`, `EXECUTION_TIMEOUT_SECONDS`, `VERIFY_WINDOW_SECONDS`, `VERIFY_TIMEOUT_SECONDS`, plus the agent limits from `CLAUDE.md` §10.2.
- FR-011 auto-resolve: the detector's `on_recovered` hook exists but nothing resolves incidents yet (next task list).

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged: delete branches `tanzeel`/`usman`, the `contracts-v0.1.0` tag, Supabase (B0.1), Gemini key (B0.3), Firebase (A0.1); plus the decisions above and in chunks 3–6. backend-api and backend-worker are not in `copilot.yml` yet because they need the Supabase `DATABASE_URL`.

## 2026-10-21 — Cloud session 1, chunk 6: proposal validation and dry run (B2.8)

### Done
- Pushed the held chunk-5 commits once runner CI on `b7a24b3` was green (all six workflows green there).
- **B2.8** (`ce51de8`): `oncallpilot/catalogue.py` (catalogue + target allowlist + ledger rollback targets; fail closed on unreadable files), `oncallpilot/runner_link.py` (signed `dry_run` with `RUNNER_LINK_KEY`, wait 15 s, retry once, only signature-verified results with the same `request_id` are trusted), `agent/proposer.py` (`RunnerProposer`, C8 fingerprint), `db/proposals.py`, `DbEffects.propose` (pending proposal, `awaiting_approval`, `proposal.created` + `incident.updated` on consecutive versions, audit rows; `audit(proposal.rejected_by_validator)` on a validator failure). Runner refusals `ACTION_NOT_ALLOWED`/`TARGET_NOT_ALLOWED`/`VALIDATION_ERROR` → `proposal_invalid`; anything else → `dry_run_unavailable`. `tests/integration/fake_runner.py` = the `FakeRunnerConsumer`.
- Tests: 277 unit, 66 integration (agent coverage 98 %), 110 security, all passing locally.

### Decisions to confirm (Tanzeel)
- `contracts/fixtures/ws/proposal.created.json`: its `fingerprint` is not the C8 §2 hash of its own fields (it looks like a placeholder). Harmless (nothing recomputes it), but a future contract PR could regenerate it.
- When the dry run cannot fill the rollback step (e.g. no `active_release`), the proposal is stored with `rollback_step = null` (not reversible) rather than refused.
- If the incident left `investigating` while the dry run ran (e.g. auto-resolved), the proposal is not stored.

### Next
`next.md` §2: B2.9 (approvals), worker entry point (also copy `contracts/actions.yaml` and `runner/targets.yaml` into the backend image), B2.10, B2.11, B2.12.

### Waiting on Tanzeel
Unchanged: delete branches `tanzeel`/`usman`, the `contracts-v0.1.0` tag, Supabase (B0.1), Gemini key (B0.3), Firebase (A0.1); plus the decisions above and in chunks 3–5.

## 2026-10-21 — Cloud session 1, chunk 5: tools, redaction, agent effects, worker consumer, Gemini, citations

### Done
- **B2.2** (`21e9c9f`): `get_recent_deploys`, `get_service_health` (GET-only to socket-proxy-ro, projected fields only), `search_runbooks` (pgvector), `EmbeddingProvider` + fake, runbook ingestion (stale chunks marked, since `ocp_backend` cannot DELETE).
- **B2.3** (`b717029`): `agent/redaction.py` (redact, redact_payload, looks_suspicious) and the TEST-008 corpus (46 positive / 24 negative; fake tokens as `<<N>>` placeholders).
- **B2.4** (`b7a24b3`, `69f67c3`): `DbEffects` (evidence with the next ref under the row lock, redacted, flagged, summarised; hypotheses; reflection; escalation via the FR-009 table; `agent_meta`; one `llm.call` audit row per LLM call), `ToolBox`, and the worker's agent-job consumer (AI-025: single-run claims, own-pending recovery, XAUTOCLAIM 300 s, `agent_interrupted`). CI's 85 % agent coverage floor now counts unit + integration (98 %).
- **B2.5** (`b3038e6`), **B2.7** (`c0d2bb7`): Gemini adapters over REST (key in header only, retry once, schema conversion), prompts v1 + `manifest.json` (integrity checked at load; CI refuses edits to an existing version).
- B2.7 detail: `reflection_rules.yaml` + `agent/citations.py` (deterministic citation check; unsupported ×0.5; no valid citation → dropped).
- CI green on `b7a24b3` (backend, migrations, chaos-shop, contracts, mobile); runner was still running at the time of writing.

### Decisions to confirm (Tanzeel)
- `LLM_MODEL=gemini-2.5-flash` in `.env.example` is a placeholder: confirm the newest Flash-class model on your key's tier (ADR-13), then B2.13 pins it.
- The citation table in `backend/oncallpilot/agent/reflection_rules.yaml` is my reading of §5.5 (only `bad_deploy` was specified); review it before the benchmark freeze.
- Until B2.8 exists, every proposal escalates `dry_run_unavailable` (an explicit stub, `UnavailableProposer`).

### Next
`next.md` §2: B2.8 (proposal validation + dry run), B2.9 (approvals), worker entry point, B2.10, B2.11, B2.12.

### Waiting on Tanzeel
Unchanged: delete branches `tanzeel`/`usman`, the `contracts-v0.1.0` tag, Supabase (B0.1), Gemini key (B0.3), Firebase (A0.1); plus the decisions above and in chunks 3–4.

## 2026-10-21 — Cloud session 1, chunk 4: observability, tools, agent state machine

### Done
- **B1.5** (`e553d9e`, fix `24c1188`): Prometheus, Loki, Alloy, Grafana in `observability.yml` (profile `obs`), configs in `infrastructure/observability/`. **S0.7 (a) run**: the ro proxy refused `GET /containers/{id}/logs` → **ADR-21** adds `ALLOW_LOGS=1` (still GET-only); `NETWORKS=1` confirmed needed. TEST-015 ro half: `tests/security/check_ro_proxy.py`, 52/52, in CI. **ADR-22** records the detector warm-up decision. Results in `docs/checks/proxy.md` (a).
- **CI break and fix:** `e553d9e` redeclared `chaos_net` in `observability.yml`; Compose v2 on GitHub runners refuses that ("conflicts with imported resource"), Compose v5 locally did not. Fixed in `24c1188` and checked with Compose v2.38.2. All six workflows were green on `7ccd714`; backend, migrations, chaos-shop, contracts and mobile are green on `24c1188` (runner was still running at the time of writing).
- **B1.8** (`fef3df9`): `query_logs` and `query_metrics` (fixed LogQL/PromQL templates, injection-safe `contains`, window clamp, ≤ 50 lines / ≤ 120 points, baseline/peak/% change/change point), Prometheus and Loki clients.
- **B2.1** (`1f9f2e1`): pure agent state machine, `Investigation` orchestrator, `AgentEffects`, `LLMProvider` + `FakeProvider`, structured output models, reflection code rules, tool registry (AI-004), untrusted wrapping (AI-011). 82 tests, agent coverage 99 %; CI floor 85 %.

### Decisions to confirm (Tanzeel)
- **ADR-21** (ro proxy `ALLOW_LOGS=1`) and **ADR-22** (warm-up keeps learning normal samples): both in `docs/adr/`, marked "please review".
- Grafana is view-only (anonymous Viewer, no login form), so no new secret was needed.
- New env names for the worker, not in the architecture yet: `PROMETHEUS_URL`, `LOKI_URL` (will be set in `copilot.yml` with the worker).
- Agent: a hypothesis set with no valid citation escalates `low_confidence` (§6.1 says "none valid" but names no reason); a gather turn with no tool calls counts as "done".

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged: delete branches `tanzeel`/`usman` in the GitHub UI, `contracts-v0.1.0` tag, Supabase (B0.1), Gemini key (B0.3), Firebase (A0.1); plus the decisions above and in chunk 3.

## 2026-10-21 — Cloud session 1, chunk 3: WebSocket gateway, seed-incident, ingest, detector

### Done
- **B1.3 part 2** (`f04bc0c`): `oncallpilot/events.py` (`EventStream`: validated publish to `ocp:events`, read, resume window) and `/ws/incidents` (auth 5 s → 4408, invalid → 4401, refresh, expiry → 4401, hello, resume/resync, ping 15 s, 2 missed pongs → close). API-014…API-017. 21 unit + 13 Redis integration tests, including "disconnect for 10 events, resume, exactly those 10".
- **B1.4** (`7ccd714`): `python -m oncallpilot.seed`, `backend/Dockerfile`, `backend-seed` (profile `tools`) in `copilot.yml`, `ocp seed-incident <name|path> [--new-ids] [--realtime]`. Checked by hand end to end.
- **B1.6** (`f352b83`): `POST /ingest/alert` (token first, then body; open with E1 + audit + job + `incident.opened`, or deduplicate; races settled by the partial unique index), severity (FR-006), window (FR-007), `backend/config/detector.yaml`, `detector_config_version` (ADR-20).
- **B1.7** (`6638e91`): detector engine, loop, ledger reader (see the commit message for the rules).
- CI: all green on `f04bc0c` except runner, whose 15-minute `runner-live` job kept being cancelled by newer pushes (`concurrency: cancel-in-progress`). On `7ccd714` its `runner` and `runner-proxy` jobs passed and `runner-live` got through `ocp up` (now with copilot-redis); pushes were held until it finished.

### Decisions to confirm (Tanzeel)
- **FR-003 warm-up:** baselines keep learning normal samples during warm-up instead of relearning from scratch; otherwise FR-003's own acceptance (fault injected right after reset must fire within 60 s after warm-up) cannot pass. Written in `oncallpilot/detector/engine.py`; `docs/architecture.md` §6.8 still says "re-learn".
- **Contract gap, FR-006 sev1 "no running container":** `AlertIn` cannot say that no container is running (a `Signal` has only value/baseline/z-score), so ingestion cannot pick sev1 for it. Options: (a) a contract change: let the `restarts` signal carry `value = 0` running containers in a documented way, or add an optional field; (b) the detector sends `error_rate = 1.0` alongside. Needs a `contract/*` change (`CLAUDE.md` §8.4); not done.
- **WebSocket close code for missed pongs:** 1008 (C3 §7 only says "any other").

### Next
`next.md` §2.

### Waiting on Tanzeel
Unchanged (delete branches `tanzeel`/`usman` in the GitHub UI, `contracts-v0.1.0` tag, Supabase B0.1, Gemini key B0.3, Firebase A0.1), plus the decisions above.

## 2026-10-21 — Cloud session 1, chunk 2: B1.2 API skeleton, B1.3 copilot-redis

### Done
- **Followed Tanzeel's new `CLAUDE.md` §11.5**: commits are authored as Tanzeel Hussain with no AI attribution lines. Tanzeel rewrote the first four commits of this session on `main`; their new hashes are `c797304` (CLAUDE.md rules), `59f01ba` (backend skeleton), `70bc7dc` (B1.1 migrations), `37d1c79` (session files). The hashes in the entry below are the pre-rewrite ones.
- **B1.2 backend-api skeleton** (`9721d8a`): settings, JSON logs, JWKS auth (SEC-010), the error envelope (API-001), `/healthz` (API-002), `GET /incidents` with cursor pagination (API-004), `GET /incidents/{id}` (API-005), and `oncallpilot.db.seed` (writes a timeline's final `IncidentDetail`; B1.4 will use it). Tests: `backend/tests` (44) and `tests/integration` (10, real Postgres). `backend.yml` runs them. CI green on `9721d8a` except the runner workflow, still running at the time of writing.
- **B1.3 part 1, copilot-redis** (`012fb40`): `infrastructure/compose/copilot.yml`, `infrastructure/redis/users.acl` + `start-redis.sh` (passwords taken from `REDIS_URL` / `RUNNER_REDIS_URL`, never in git), and `tests/security/test_redis_acl.py` (SEC-004, 22 tests). The dummy Redis passwords in `.env.example` now differ (the start script refuses equal ones).
- The runner workflow failed once on `52d8c9a` because a registry answered `toomanyrequests` while pulling socket-proxy images (before any test ran); the failed jobs were re-run once.

### Next
`next.md` §2: B1.3 part 2 (`ocp:events` publisher and the `/ws/incidents` gateway), then B1.4, B1.6, B1.7, B1.5, B1.8.

### Waiting on Tanzeel
Unchanged from the entry below (delete the old branches, the contracts tag, Supabase, Gemini key, Firebase).

## 2026-10-21 — Cloud session 1: single developer, branches, B1.1 migrations

### Done
- **`CLAUDE.md` §1, §8.1, §11 rewritten** for one developer: commit and push directly to `main`, test before each push, no force-push, tags only on request (`6b268d9`).
- **Branches `tanzeel` and `usman`:** both are fully merged into `main` (0 commits ahead), but **deleting them failed**: `git push origin --delete tanzeel usman` got HTTP 403 from this session's git proxy (a policy refusal, not retried). Tanzeel deletes them by hand (below).
- **CI:** the chaos-shop job was already green on `main` (`a96be5f`); Tanzeel's DOCKER_API_VERSION fix (`9ff85a0`) solved it. All six workflows passed on `a96be5f`.
- **B1.1 migrations** (`f1bfbf0`, after the backend package skeleton `76f75ab`): `supabase/migrations/20261021190000_initial_schema.sql` with all 10 tables, enums, triggers, RLS, and the `ocp_backend` role; `tests/db/` with 70 negative/positive tests (TEST-016); `migrations.yml` now checks forward-only edits (DB-013), applies the files with psql, and runs the suite. `OCP_TEST_DATABASE_URL` added to `.env.example`.

### Next
See `next.md` §2: B1.2 backend-api skeleton, then B1.3, B1.4, B1.6, B1.7, B1.5, B1.8.

### Waiting on Tanzeel
- **Delete the merged branches** `tanzeel` and `usman` in the GitHub web UI (Code → Branches → bin icon), since the session's proxy refused the delete.
- **Tag `contracts-v0.1.0`** on `main` when you are happy with the contracts (`git tag contracts-v0.1.0 && git push origin contracts-v0.1.0`), or tell a session to do it.
- **B0.1 Supabase:** create `ocp-dev` and `ocp-bench`; disable sign-ups; switch both to an asymmetric JWT signing key; create your user and a test user (`OCP_TEST_EMAIL` / `OCP_TEST_PASSWORD` in `.env.local`); leave `ocp` out of the Data API's exposed schemas; apply the migration with `supabase db push`; then give the `ocp_backend` role a login and password by hand (`alter role ocp_backend with login password '…'`) and put the session-pooler URL in `.env` as `DATABASE_URL`. Then check DB-012 (anon REST call fails) and DB-014.
- **B0.3:** a Gemini API key (LLM and embeddings) in `.env.local`, for S0.7 (c) and B2.5.
- **A0.1 Firebase:** `google-services.json` and the FCM service account in `infrastructure/secrets/` (for B2.11 and the app).

## 2026-10-10 — Session 3: PR branches allowed, Docker check, Phase 0 status

### Done
- **Merged `origin/tanzeel`** (`0ca3034`): one new commit, `b71ed6b`, runner coverage measured with pytest-cov (CI enforces 85 %). Clean merge.
- **Relaxed `CLAUDE.md` §11.1, as Usman approved** (`406a8bb`). Besides `usman`, his PR branches from §8.1 (`contract/*`, `feat/b-*`, `fix/*`, `chore/*`, `docs/*`, `test/*`, `bench/*`) may now be pushed. Direct pushes or merges to `main` or `tanzeel`, `feat/a-*`, and tags stay forbidden; a PR is opened only when Usman asks, and for Phase 0 only after Tanzeel's go. The local pre-push hook was updated to match and tested against each kind of target.
- **Checked Docker.** Docker Desktop is installed (client 29.8.2, Compose v5.5.1, in `%LOCALAPPDATA%\Programs\DockerDesktop`), but **the engine is not running.** WSL 2 is not installed (`wsl.exe` offers only `--install`), and Docker Desktop on Windows Home needs it. Its `bin` folder is also not on this terminal's PATH yet.
- **CI on `tanzeel`:** the `chaos-shop` job is still red on his newest commit `b71ed6b`; the other five jobs are green.

### Phase 0 status (definition of done, `phases.md` §5)
| Item | Status |
|---|---|
| All contracts C1–C9 read by the other developer | ✅ both written reviews done (`contract-review-a.md`, `contract-review-b.md`); a written review replaced the walkthrough, as Tanzeel decided |
| All contracts merged into `main`, tag `contracts-v0.1.0` | ❌ waits for Tanzeel's go, his red chaos-shop job, and the PRs |
| Python fixtures validate against the schemas | ✅ 259 contract tests |
| Dart parses the fixtures | ✅ Tanzeel's `mobile/test/contracts/fixtures_test.dart`; mobile CI green |
| Phone reached the laptop (S0.5) | ✅ |
| CI skeleton green | ⚠️ 5 of 6 jobs green; chaos-shop red (Tanzeel) |
| S0.6 Docker Desktop on Usman's laptop | ⚠️ installed, not running: needs WSL 2 |
| B0.1 Supabase projects · B0.3 API keys | ❌ Usman, by hand |
| S0.7 (a) ro proxy + Alloy · (c) embeddings · (d) Supabase JWKS | ❌ need Docker · the Gemini key · Supabase |

**Phase 0 is not complete.** What closes it: WSL 2 → Docker running → S0.7 (a); the Gemini key → S0.7 (c); Supabase → S0.7 (d); Tanzeel fixes chaos-shop CI and says go → PRs into `main` → tag `contracts-v0.1.0`.

### Next
**`next.md` has the step-by-step version.**
1. **Usman:** install WSL 2 and start Docker Desktop (steps below). Then get a Gemini key and create the Supabase projects (the steps in the session 1 entry).
2. **Next session:** run S0.7 (a) once `docker version` shows a server; (c) and (d) once the key and Supabase exist.
3. **When Tanzeel says go:** open the Phase 0 PRs into `main` as `next.md` §2 says.

### Waiting on
- **Usman:** WSL 2 + Docker Desktop running; the Gemini key; Supabase.
- **Tanzeel:**
  - the red chaos-shop CI job;
  - his go-ahead for the merge;
  - the C7 deviations written into `telemetry.md`;
  - the C5 issue-9 rule in the runner;
  - the `chaos-shop/cli/uv.lock` refresh in the contracts PR.

### Usman's steps: get Docker running (Windows 10 Home)
1. Open PowerShell **as Administrator** and run `wsl --install --no-distribution`. Restart the laptop when it finishes.
2. Start **Docker Desktop** from the Start menu. Accept the licence and choose the WSL 2 backend if asked, then wait until it shows "Engine running".
3. Open a **new** terminal (so PATH picks up Docker) and check:
   - `docker version` shows a *Server* section;
   - `docker compose version` shows v2.20 or newer;
   - `docker run --rm hello-world` prints its greeting.
4. In the repository root, copy `.env.example` to `.env` and replace the four testbed values with your own random hex (`python -c "import secrets; print(secrets.token_hex(24))"`).
5. Tell Claude "Docker is running".

### Message for Tanzeel
> Quick update from my side: I merged your `b71ed6b` (clean). Your **chaos-shop CI job is still red** on it, the same as on `1a4f03a`; the other five are green. Can you check that log? Once it's green and you say go, I'll open my contract PRs into `main`. My rules now allow PR branches (`contract/*` and so on); nothing goes to `main` directly.

## 2026-10-10 — Session 2: merged Tanzeel's work, answered his contract review

### Done
- **Merged `origin/tanzeel` into `usman`** (`d5c49a0`): 44 commits (A1.2–A1.6, A2.1 runner, `ocp up/down/doctor`, Gate G1 checks, the Dart fixture test). There were no conflicts; the contract gate stayed green.
- **Reviewed C4, C6, and C7:** C4 and C6 approved; C7 approved, provided Tanzeel writes his two accepted deviations into `telemetry.md`. See `docs/checks/contract-review-b.md` (`f27d37d`).
- **Answered all 11 issues** of `docs/checks/contract-review-a.md`. Ten are fixed and issue 8 is accepted for later; the per-issue table is in `contract-review-b.md`.

  | Commit | Fix |
  |---|---|
  | `3e33a3e` | C1: `DeployListItem` gets `replicas` and `reason`; metric `service` is `api`/`worker` only; a new `unit` (`METRIC_UNITS`); `MonitorSettings.lock_reason`; timelines use the testbed's real release history and the real 1.5.0 `IndexError`, with relative-time summaries |
  | `aa91cd9` | C3: the 4 KB cut of `evidence.added` payloads is defined (`cut_evidence_payload()`, `ws-protocol.md` §4.1) |
  | `857b775` | C5: a refused or aborted `execute` claims its idempotency key; unanswerable messages get no result; a different `catalogue_version` is refused |

- **Fixed a CI break that my contracts caused:** `chaos-shop/cli/uv.lock` was stale because the contracts package gained `pyyaml` dev dependencies (`ae54f2f`, in Tanzeel's folder, done at Usman's request).
- **Checks:**
  - Contract gate: ruff, `mypy --strict`, **259 tests**, schemas regenerate with no diff.
  - Locally, the non-Docker CI steps of chaos-shop (77 tests), the chaos CLI (37), and `ocp` (24) all pass, and every `uv.lock` passes `uv lock --check`.
- **Pushed `usman`.** CI does not run on it, because the workflows run only on PRs and on `main`.
- **CI on `tanzeel`** (`1a4f03a`): backend, migrations, mobile, contracts, and runner pass; **chaos-shop fails**. The log needs admin rights, and it is not one of the steps that can run here without Docker. It is Tanzeel's to check.

### Next
1. Send Tanzeel the message below.
2. When Tanzeel says go: the contracts go in through `contract/*` PRs and the rest through PRs, each merged once CI is green. This needs Usman to relax `CLAUDE.md` §11.1 (push only `usman`) to allow those PR branches; ask him first. Nothing is pushed to `main` directly.
3. Then: the `contracts-v0.1.0` tag, and Phase 1 for Stream B.
4. Still open: Docker Desktop, the Gemini key, and Supabase (Usman, by hand), then S0.7 (a), (c), and (d) (`next.md`).

### Waiting on
- **Tanzeel:**
  - his go-ahead for the merge;
  - fix the red chaos-shop CI job on `tanzeel`;
  - refresh `chaos-shop/cli/uv.lock` in the same PR as the contracts;
  - write the C7 deviations into `telemetry.md`;
  - add the C5 idempotency rule (issue 9) to the runner's refusal path.
- **Usman:** Docker Desktop, the Gemini key, and the Supabase projects; and the §11.1 decision when the merge starts.

### Message for Tanzeel

> Hi Tanzeel, done on `usman` (pushed, `ae54f2f`). I merged your branch first: no conflicts.
> 1. **C4, C6, C7:** C4 and C6 approved. C7 approved, provided you write your two accepted deviations into `telemetry.md` (no access line for `/metrics` and `/internal/*`; no `route` on cs-payments lines). Details in `docs/checks/contract-review-b.md`.
> 2. **Your 11 issues:** 1–7 and 9–11 are fixed, and 8 is accepted for a later PR (tell me which fixtures you need first). The per-issue replies with commits are in `contract-review-b.md`. Both must-fixes are in: the 4 KB cut is defined so a cut payload still validates (`cut_evidence_payload()`), and the recent-deploys record now carries `replicas` and `reason`. New fields: `unit`, `replicas`, `reason`, `lock_reason`; your DTOs ignore unknown keys, so your fixture test still passes. For issue 9, your runner's refusal path needs the new rule: claim `ocp:runner:idem:{execution_id}` before sending a refused or aborted result.
> 3. **CI:**
>    - Contracts are green locally: 259 tests. CI hasn't run on `usman`, because it only runs on PRs.
>    - **Your chaos-shop job is red on `tanzeel` (`1a4f03a`).** The non-Docker steps pass on my machine, so it's probably a Docker step (compose config, nginx `-t`, or the image build). Can you check the log? I can't open it without admin rights.
>    - Because my contracts package added `pyyaml` dev dependencies, `chaos-shop/cli/uv.lock` must be regenerated (`cd chaos-shop/cli && uv lock`, a two-line change) in the same PR that brings the contracts to `main`; otherwise `uv sync --locked` fails. I committed that refresh on `usman` (`ae54f2f`).
> 4. **Merge:** I won't touch `main` until you say so. Contracts through `contract/*` PRs, the rest through PRs.
>
> Also answered in `contract-review-b.md`: `chaos_net` declared plainly; ledger volume read-only at `/ledger`; `restart_count` over `OOMKilled`; scenario 5 by p95; `tools/ocp` as the single CLI (I'll add `seed-incident` there); and your runbook split is fine (format in §3).

## 2026-10-09 — Session 1: branch setup, contracts C1/C2/C3/C5/C8/C9, socket-proxy-ro

### Done
- **Set up this folder** as a git checkout of the new branch `usman`, started from `origin/tanzeel` at `6cac236`, so Tanzeel's 16 GB edits are in. The five loose copies of the docs were older (they still said 20 GB) and byte-identical to commit `91dddda8`, so nothing was lost; they are also backed up in the session scratchpad. Added a local pre-push hook that allows pushing only `usman`, and checked that it refuses `main`, `tanzeel`, and tags.
- **Commits on `usman`** (all pushed to `origin/usman`):

  | Commit | What |
  |---|---|
  | `524bdcb` | `CLAUDE.md` §11 (Usman's working rules) and the three session files |
  | `e99693b` | C1 domain models and enums (17 schemas) |
  | `2efbbe2` | C5 runner messages, HMAC signing, 7 test vectors |
  | `674dbfd` | C8 approval protocol (`approval-protocol.md`) |
  | `d257212` | C9 push payload (`push.md`) |
  | `adb9388` | C3 WebSocket protocol (`ws-protocol.md`) and 14 WS fixtures |
  | `c58495a` | C2 REST API (`openapi.yaml`) |
  | `36db805` | The 3 Phase 0 timeline fixtures |
  | `42392f8` | socket-proxy-ro in `infrastructure/compose/observability.yml`, `include:` line, `.env.example` |
  | `8de9823` | Stream B progress table in `docs/checks/week-1.md` |

- **Checks run:**
  - The contract gate (the same steps as `contracts.yml`) is **green**: ruff, `ruff format --check`, `mypy --strict`, **246 tests** (36 of Tanzeel's plus 210 new), schemas regenerate with no diff, and `uv sync --locked` passes.
  - `openapi.yaml` passes `openapi-spec-validator`.
  - The compose files were parsed and the proxy's safety settings checked by script. They were **not** run with Docker, because Docker is not installed here.
- **Installed** `uv` 0.12.7 with pip; `uv` downloaded Python 3.12.14 for the contracts environment.
- **Read** Tanzeel's C4, C6, and C7 as their reader. Nothing is blocking; the notes are below.
- **Explained Docker and Compose** in `learn.md` (section 3, "Docker, in more detail"), as Usman asked.
- **Wrote `next.md`**, the step-by-step plan for the next Claude session, as Usman asked. While writing it I found 4 new commits on `tanzeel` (A1.2: `cs-lb` and all 12 slot containers). They are not merged yet; a trial merge with `git merge-tree` was clean. They also bring two decisions for Usman: Tanzeel's "C7 deviations".

### Next
**`next.md` has the detailed, step-by-step version of this list for the next Claude session.**
1. **Send Tanzeel the message below** and agree a time for the 30-minute fixture walkthrough.
2. **Usman, by hand** (steps below): install Docker Desktop (S0.6), get a Gemini API key (B0.3), and create the two Supabase projects (B0.1).
3. **Next session, once 2 is done:**
   - S0.7 (a): start socket-proxy-ro with debug logging and a temporary Alloy (`discovery.docker` + `loki.source.docker`). Record the endpoints Alloy calls and a re-test with `NETWORKS=0`. Check that POST returns 403, that `/_ping` and `/version` return 200, and that `/libpod/*` returns 403. Write the results in `docs/checks/proxy.md`, section "(a) socket-proxy-ro and Alloy — Usman". Also run `docker compose --profile obs config`.
   - S0.7 (c): check that `gemini-embedding-001` with `output_dimensionality=768` returns 768 numbers that need L2 normalisation.
   - S0.7 (d): sign in to `ocp-dev`, then verify the token through the live JWKS (also SEC-010 in `week-1.md`).
   - If (a) shows `NETWORKS=1` is not needed, removing it changes architecture §2.11 and SEC-014, so ask Usman first.
4. **After the walkthrough:** apply the agreed contract changes. When Usman says so (`CLAUDE.md` §11.1), move the contract commits into `contract/*` PRs, merge, and tag `contracts-v0.1.0`. Then Phase 1 starts with B1.1 (migrations).
5. **Optional:** in the GitHub web UI, open a draft PR `usman → main` titled "DRAFT, do not merge", like Tanzeel's PR #1. CI workflows run only on PRs, so this is how the checks run on GitHub. This laptop has no `gh` CLI.

### Waiting on
- **Usman:**
  - Decide on Tanzeel's two C7 deviations (`next.md` §2): amend `contracts/telemetry.md` to match his code (recommended), or ask him to change the code.
  - Docker Desktop, a Gemini key, and the Supabase projects (steps below).
  - Whether to open the optional draft PR.
  - Your GitHub username, if you want the Usman lines in `.github/CODEOWNERS` switched on. Tanzeel left them commented out with a TODO.
- **Tanzeel:**
  - Read C1, C2, C3, C5, C8, and C9 (he is their reader), and answer the walkthrough questions in the message below.
  - The read-only proxy half of TEST-015 ("any POST through socket-proxy-ro → 403") goes in his `runner/tests/security/test_proxy.py` once socket-proxy-ro runs. Alternatively, Usman adds it under `tests/security/`; agree which.

### Message for Tanzeel

> Hi Tanzeel, my contracts are on my branch **`usman`**. I branched from `tanzeel` first, so your 16 GB edits are in.
> - **C1** `domain.py` + `enums.py`, **C2** `openapi.yaml` + `api.py`, **C3** `ws.py` + `ws-protocol.md` + `fixtures/ws/`, **C5** `runner.py` + `signing.py` + `fixtures/signing/vectors.json`, **C8** `approval.py` + `approval-protocol.md`, **C9** `push.py` + `push.md`. Every model is registered in `SCHEMAS` (40 new schemas). Contract tests: 246 green. Each contract is its own commit touching only `contracts/`, so each can become its own `contract/*` PR.
> - The **3 Phase 0 timelines** are in `contracts/fixtures/timelines/`, ready for the **30-minute walkthrough**. When suits you?
> - **socket-proxy-ro** is in `infrastructure/compose/observability.yml` with `LIBPOD_PING=0` and `LIBPOD_VERSION=0`, included from `compose.yaml`. My S0.7 results (ro proxy + Alloy, embeddings, JWKS) aren't in `proxy.md` yet: Docker isn't installed on my laptop yet.
>
> **Questions for the walkthrough:**
> 1. C5: I added `captured` to `execution_result`, because your C4 `rollback_step` reads `captured.previous_replicas` / `captured.previous_release`. OK?
> 2. C5: for a `failed` result (a step or the structural check failed) I left `error_code` null. Do you want codes for it?
> 3. C1/C5: the dry-run fields are my proposal, and you produce them: `would_change {target, change}`, `current {active_release, running_replicas, containers, cache_keys}`, `preconditions {rate_limit_ok, cooldown_ok, slots_available}`. The step statuses are `running`, `succeeded`, `failed`. Change anything you like.
> 4. C5: stream entries carry the message as JSON in one field named `msg`. The signature covers the JSON object as sent; keys are hex in the env vars.
> 5. Timelines: a step with `await_approval: true` marks where mock mode waits for the user's Approve. Does that fit `MockIncidentRepository`?
> 6. After signal verification fails, I left the original proposal `succeeded`; the failure shows in `health_after.verdict = fail` and the incident is `action_failed` (`scale_failed_rollback`). OK for the Execution Status screen?
> 7. Schema files are snake_case like your `deploy_record.json`, so API-005's `IncidentDetail.json` is `incident_detail.json`.
>
> **Reader notes on your C4, C6, C7:** all good, nothing blocking. On my side I'll set `honor_labels: true` on the Prometheus jobs (C7 §2.2), and `query_logs` with `level=ERROR` will also match `CRITICAL`, since a startup crash logs at CRITICAL.

### Usman's manual steps

**S0.6 — Docker Desktop** (needed for S0.7 (a) and for all of Stream B's local stack):
1. Windows 10 Home needs WSL 2. In PowerShell **as Administrator**, run `wsl --install`, then restart.
2. Install Docker Desktop for Windows from docker.com and choose the WSL 2 backend. Restart if asked.
3. Check in a terminal: `docker version` (note the *Server API version*) and `docker compose version` (must be 2.20 or newer, for `include:`).
4. Copy `.env.example` to a root `.env` (git-ignored) and replace the four testbed values with your own random hex, made with `python -c "import secrets; print(secrets.token_hex(24))"`. Compose reads every included file, so it asks for these even when you start only `socket-proxy-ro`.
5. If drive C: is short of space, move the disk image: Docker Desktop → Settings → Resources → Advanced.
6. Tell Claude "Docker is installed", and S0.7 (a) can run.

**B0.3 — Gemini API key** (one key serves the LLM and embeddings, ADR-13/14):
1. Create an API key in Google AI Studio.
2. In the repository root, create a file named `.env.local`. It is git-ignored by the `.env.*` rule. Add the line `GEMINI_API_KEY=<your key>`.
3. Never paste the key into the chat, a commit, or a prompt.

**B0.1 — Supabase** (`ocp-dev` and `ocp-bench`, both the same way):
1. Create both projects on supabase.com.
2. In Authentication settings, **turn off new-user sign-ups** (SEC-016).
3. In the project's JWT signing-key settings, **switch to an asymmetric signing key** (ECC or RSA). Without it the JWKS has no public key and backend auth cannot work (architecture §7.11).
4. In Authentication → Users, add users by hand: Usman, Tanzeel, and one **test user** for `ocp smoke` and `bench`.
5. Add these lines to `.env.local` (values from the `ocp-dev` project settings):
   - `SUPABASE_URL=…`
   - `SUPABASE_ANON_KEY=…` (the public anon key)
   - `OCP_TEST_EMAIL=…`
   - `OCP_TEST_PASSWORD=…`

### Notes for Usman's later tasks (found today)
- **B1.5:** the Prometheus jobs for `api-upstream` and `worker-upstream` need `honor_labels: true` (C7 §2.2).
- **B1.8:** the `query_logs` level filter `ERROR` should match `ERROR|CRITICAL`, because a startup crash (scenario 6) logs at `CRITICAL`. Non-JSON lines are labelled `UNKNOWN` and are reachable only with `ANY`.
- **B2.10:** RUN-015 says invalid runner results are dropped "with an audit entry", but §7.9 has no event name for that. Add one through a contract change when building it.
