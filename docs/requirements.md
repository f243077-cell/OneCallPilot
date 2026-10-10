# OnCallPilot — Technical Requirements Specification

> **Version:** 1.0 · **Source of names:** `architecture.md`. Section references below (§) point to that file.
> **Priority:** **Must** = needed for the definition of done · **Should** = expected unless time runs out · **Could** = first thing to cut.
> **Owners:** U = Usman (Stream B), T = Tanzeel (Stream A), U+T = shared.
> **Keywords:** MUST, MUST NOT, and SHOULD are used in the RFC 2119 sense. Every acceptance criterion is meant to become a test or a scripted check.

## Contents
1. [FR — Incident lifecycle and detection](#1-fr--incident-lifecycle-and-detection)
2. [AI — Agent](#2-ai--agent)
3. [API — REST and WebSocket](#3-api--rest-and-websocket)
4. [DB — Database](#4-db--database)
5. [MOB — Mobile app](#5-mob--mobile-app)
6. [RUN — Runner](#6-run--runner)
7. [SEC — Security](#7-sec--security)
8. [TB — Testbed and observability](#8-tb--testbed-and-observability)
9. [NFR — Non-functional](#9-nfr--non-functional)
10. [TEST — Testing](#10-test--testing)
11. [BENCH — Benchmark](#11-bench--benchmark)
12. [Traceability: spec feature → requirements](#12-traceability)

---

## 1. FR — Incident lifecycle and detection

#### FR-001 — Detector polling loop
`backend-worker` · U · **Must** · Depends: TB-003, TB-012
The detector MUST evaluate every signal in §6.8 for each monitored service every `DETECTOR_INTERVAL_SECONDS` (default 5).
**Accept:** (1) With the testbed at baseline, the detector logs one evaluation cycle per interval (±1 s). (2) A Prometheus or Loki query failure is logged and the cycle continues; the loop never crashes.

#### FR-002 — EWMA and z-score trigger rules
`backend-worker` · U · **Must** · Depends: FR-001
Each signal MUST use EWMA (α = 0.1) with z-score triggers, absolute floors, and consecutive-sample rules as defined in `backend/config/detector.yaml` (defaults in §6.8). Mean and variance MUST freeze while a signal is anomalous.
**Accept:** Unit tests with synthetic series show that (1) a step change to 31 % error rate triggers within 3 samples, (2) noise within ±3σ never triggers over 10,000 samples, and (3) a 1.5 % error rate does not trigger, because the 2 % floor applies.

#### FR-003 — Warm-up after reset
`backend-worker` · U · **Must** · Depends: TB-009
The detector MUST NOT open incidents for 180 s after a `kind=reset` record appears in the deploy ledger, and MUST re-learn baselines during that window.
**Accept:** `chaos reset` followed by immediate fault injection opens no incident before t = 180 s, and opens one within 60 s after warm-up ends.

#### FR-004 — Alert ingestion creates an incident
`backend-api` · U · **Must** · Depends: API-003, DB-002, DB-003
`POST /ingest/alert` MUST, in one transaction, insert an incident (`status=investigating`), seed evidence `E1` (`kind=detector_signal`, `purpose=seed`), and write `audit(incident.opened)`. After commit it MUST enqueue `ocp:agent:jobs` and publish `incident.opened`.
**Accept:** An integration test posts an alert and finds exactly one incident, one evidence row with `ref=E1`, one audit row, one agent job, and one `ocp:events` entry.

#### FR-005 — Deduplication per service
`backend-api` · U · **Must** · Depends: FR-004, DB-002
If an incident for the same `service` is not `resolved`, ingestion MUST NOT create a new one. It MUST return `202 {incident_id, deduplicated:true}` and append the signals to `trigger.updates`.
**Accept:** 20 identical alerts within a minute → 1 incident; the database partial unique index rejects a second active incident even if application logic is bypassed.

#### FR-006 — Severity computation
`backend-api` · U · **Must** · Depends: FR-004
Severity MUST be: `sev1` if a service has no running container or error rate ≥ 25 %; `sev2` if error rate ≥ 5 %, p95 ≥ 3× baseline, or restarts are present; otherwise `sev3`.
**Accept:** A table-driven unit test covers each branch and its boundaries (24.9 % / 25 %).

#### FR-007 — Incident window
`backend-api` · U · **Must** · Depends: FR-004
`window_start` MUST be the earliest `first_anomalous_at` minus 10 minutes. `window_end` MUST stay null until the incident resolves.
**Accept:** Unit test on window computation; resolved incidents have a non-null `window_end`.

#### FR-008 — Alertmanager webhook adapter
`backend-api` · U · **Could** · Depends: API-003
`POST /ingest/alert` SHOULD also accept Alertmanager webhook v4 payloads and map `labels.service` to `service`.
**Accept:** A fixture Alertmanager payload creates an incident identical in shape to the native format.

#### FR-009 — Incident status transitions
`backend` · U · **Must** · Depends: DB-002
Only these transitions are allowed:
- `investigating` → `awaiting_approval` | `escalated` | `resolved`
- `awaiting_approval` → `executing` | `escalated` | `resolved`
- `executing` → `verifying` | `action_failed` | `awaiting_approval` (only after `STATE_DRIFT`, FR-017) | `escalated`
- `verifying` → `resolved` | `action_failed` | `escalated` (only when a rollback's verification fails, FR-016)
- `action_failed` → `awaiting_approval` | `escalated` | `resolved`
- `escalated` → `resolved`

Each transition MUST increment `state_version` and write `audit(incident.status_changed)`.
**Accept:** A transition-table test rejects every other pair with a domain error.

#### FR-010 — Escalation reasons
`backend-worker` · U · **Must** · Depends: FR-009
Escalation MUST set `status_reason` to one of the reasons in §5.7, keep all evidence and hypotheses, publish `incident.updated`, and send a "Needs human investigation" push.
**Accept:** For each reason, a test drives the agent or flow into that branch and asserts the stored reason and the push.

#### FR-011 — Auto-resolve on recovery
`backend-worker` · U · **Must** · Depends: FR-001
Any non-resolved incident with no execution in progress, whose triggering signals stay normal for 3 consecutive minutes, MUST become `resolved` with `resolution=auto_recovered`.
**Accept:** Manual-mode run where an operator fixes the fault via the terminal → incident auto-resolves within 3–4 minutes.

#### FR-012 — Manual resolve
`backend-api` · U · **Should** · Depends: API-007
An authenticated user MUST be able to resolve an incident with a reason, unless an execution is `queued` or `running` (→ `409 INCIDENT_BUSY`).
**Accept:** API test for success, busy, and not-found.

#### FR-013 — Proposal expiry
`backend-worker` · U · **Must** · Depends: DB-005
Pending proposals MUST expire at `expires_at` (`PROPOSAL_TTL_SECONDS`, default 900). A sweeper running every 10 s MUST set them to `expired`, set the incident to `escalated` (`proposal_expired`), and publish `proposal.updated` and `incident.updated`.
**Accept:** With TTL set to 5 s in a test, an unapproved proposal is expired within 15 s, and a later approve returns `409 PROPOSAL_EXPIRED`.

#### FR-014 — Two-stage health check
`backend-worker`, `runner` · U+T · **Must** · Depends: RUN-014
After every execution, the structural result (from the runner) and the signal verification (from the worker; normal for `VERIFY_WINDOW_SECONDS` = 60 within `VERIFY_TIMEOUT_SECONDS` = 300) MUST both pass for the incident to be `resolved` with `resolution=action_succeeded`. The combined verdict MUST be stored in `executions.health_after`. Signal verification MUST be driven by database state (a poll over incidents in `verifying`), so a worker restart cannot strand an incident.
**Accept:** Scenario 2 rollback → `health_after.verdict = "pass"`. A forced failure (wrong release) → `action_failed`. Restarting the worker while an incident is `verifying` → it still ends `resolved` or `action_failed`.

#### FR-015 — Rollback offered on failed health
`backend-worker` · U · **Must** · Depends: FR-014, AI-018
When the verdict fails and the action has a `rollback_step`, the worker MUST create a `kind=rollback` proposal (with `parent_proposal_id`, tier from the catalogue), request a dry run, set the incident to `awaiting_approval`, and push. If there is no rollback step → `escalated`.
**Accept:** Test with a scale action whose verification is forced to fail → a rollback proposal with `params.replicas` equal to the captured previous count.

#### FR-016 — Post-rollback verification
`backend-worker` · U · **Must** · Depends: FR-015
After a rollback executes, the incident MUST become `resolved` (`rolled_back`) if signals are normal, otherwise `escalated` (`rolled_back_after_failed_action`).
**Accept:** Both branches covered by integration tests with a fake runner.

#### FR-017 — State drift supersedes the proposal
`backend-worker` · U · **Must** · Depends: RUN-008
When the runner aborts with `STATE_DRIFT`, the backend MUST set the proposal to `superseded`, request a fresh dry run, create a new proposal with the same action and parameters (linked via `superseded_by`), and require a new approval.
**Accept:** A test changes the ledger between approval and execution → no action is executed; a new pending proposal exists.

#### FR-018 — Push notifications
`backend` · U · **Must** · Depends: SEC-009, MOB-016
The backend MUST send FCM pushes (§9.7) for `incident.opened`, `proposal.created` (including rollback proposals), escalation, `action_failed`, and `incident.resolved` to every device with `push_enabled=true`. Tokens rejected as unregistered MUST be deleted.
**Accept:** With an FCM fake, each event produces exactly one message per device with only the allowed keys.

#### FR-019 — Event publication
`backend` · U · **Must** · Depends: API-015
Every persisted change to an incident aggregate MUST publish exactly one event of the matching type to `ocp:events` after commit.
**Accept:** An integration test collects all events for a scenario-2 replay and matches the expected ordered list.

#### FR-020 — Incident history retention
`backend`, `db` · U · **Must** · Depends: DB-011
Incidents and all child records MUST be retained; no API or role can delete them.
**Accept:** `DELETE` on any `ocp` table as `ocp_backend` fails with a permission error.

#### FR-021 — Benchmark run tagging
`backend-api` · U · **Must** · Depends: BENCH-004
On incident creation, if `ocp:bench:current_run` is set, its value MUST be stored in `incidents.benchmark_run_id`.
**Accept:** `bench run` sets the key; the created incident carries the run ID.

#### FR-022 — Agent-disabled mode
`backend` · U · **Must** · Depends: BENCH-005
With `AGENT_ENABLED=false`, incidents and pushes are still created, but no agent job is enqueued and no proposal can exist.
**Accept:** Manual-mode run produces an incident with no hypotheses or proposals.

#### FR-023 — Monitor settings
`backend-api` · U · **Should** · Depends: API-013, DB-010
Settings (monitored services, detector thresholds, minimum push severity) MUST be readable. They MUST be updatable only when `BENCHMARK_LOCK=false`. Each update MUST bump `version` and change `detector_config_version` for new incidents.
**Accept:** `PUT` while locked → `423 BENCHMARK_LOCKED`; `PUT` with a stale version → `409 VERSION_CONFLICT`.

#### FR-024 — Runner result handling and timeout
`backend-worker` · U · **Must** · Depends: FR-009, FR-013, RUN-015
The worker MUST handle a verified final runner result by its status (`architecture.md` §5.10 step 6): `succeeded` → execution `succeeded`, incident `verifying`; `failed` → execution `failed`, incident `action_failed` (FR-015); `aborted` with `STATE_DRIFT` → FR-017; `refused`, or `aborted` with any other code → the runner changed nothing, so the execution is `failed` with the runner's `error_code`, the proposal is `failed`, no rollback is proposed, and the incident is `escalated` with reason `runner_refused`.
The sweeper that expires proposals (FR-013) MUST also end every execution that is still `queued` or `running` `EXECUTION_TIMEOUT_SECONDS` (default 240) after its creation: execution `failed` with `error_code=RUNNER_TIMEOUT`, proposal `failed`, incident `escalated` with reason `runner_timeout`, plus audit rows, `incident.updated`, and a push. It MUST NOT retry the action or propose a rollback, because the runner may already have acted. A result for an execution that has already reached a final state (a duplicate delivery, or one that arrives after the timeout) is logged and changes nothing.
**Accept:** With `FakeRunnerConsumer` scripted to answer `succeeded`, `failed`, `aborted`/`STATE_DRIFT`, and `refused`/`REQUEST_EXPIRED`, the stored statuses, reasons, and the presence or absence of a rollback proposal match the text above. With the fake silent and the timeout set to 5 s, the incident is `escalated` (`runner_timeout`) within 15 s, a manual resolve then succeeds, and a late or duplicate result changes nothing.

---

## 2. AI — Agent

#### AI-001 — Bounded state machine
`agent` · U · **Must** · Depends: —
The agent MUST implement the states and transitions in §6.1 as pure functions over an `AgentState` value, with side effects behind an `AgentEffects` interface.
**Accept:** 100 % of transitions are covered by unit tests that run without network, LLM, or database.

#### AI-002 — Tool-call budget
`agent` · U · **Must** · Depends: AI-001
The agent MUST make at most 8 tool calls per incident: ≤ 6 gather calls, exactly 1 disproof call, and at most 1 `propose_action`. Calls that are rejected because of invalid parameters still count.
**Accept:** A FakeProvider that requests tools endlessly results in exactly 6 gather calls, then hypothesis generation; `agent_meta.tool_calls ≤ 8` in every regression run.

#### AI-003 — Wall-clock budget
`agent` · U · **Must** · Depends: AI-001
The agent MUST stop within `MAX_AGENT_SECONDS` (90) of job pickup and escalate with `budget_exhausted`, keeping the evidence gathered so far.
**Accept:** A FakeProvider with a 40 s delay per call escalates at ≤ 92 s; the evidence rows remain.

#### AI-004 — Read-only toolset
`agent` · U · **Must** · Depends: §6.3
The toolset MUST be exactly: `query_logs`, `query_metrics`, `get_recent_deploys`, `get_service_health`, `search_runbooks`, `propose_action`. No tool may write to the monitored system, execute text, or call the runner.
**Accept:** A test asserts that the registered tool set equals this list; a static check (grep test) finds no `subprocess`, `os.system`, or Docker write calls under `agent/`.

#### AI-005 — `query_logs`
`agent` · U · **Must** · Depends: TB-004, TB-012
The tool MUST build LogQL from a fixed template with an enum `service` and `level`, escape `contains` as a literal (≤ 64 chars), clamp the window to the incident window ± 30 min, and cap results at 50 lines of 200 characters each.
**Accept:** An injection-style `contains` value (`"} |~ ".*`) yields a literal match query; query unit tests snapshot the LogQL.

#### AI-006 — `query_metrics`
`agent` · U · **Must** · Depends: TB-003
The tool MUST accept only the 12 template names in §6.3, render fixed PromQL, downsample to ≤ 120 points, and return baseline mean, peak, % change, and change-point time.
**Accept:** An unknown template is rejected; rendered PromQL is snapshot-tested per template.

#### AI-007 — `get_recent_deploys`
`agent` · U · **Must** · Depends: TB-009
The tool MUST read the deploy ledger read-only and return at most 10 records, newest first, optionally filtered by service.
**Accept:** Against a fixture ledger → correct order and filter; the ledger is mounted read-only (a write attempt fails).

#### AI-008 — `get_service_health`
`agent` · U · **Must** · Depends: SEC-014
The tool MUST return only the projected fields in §6.3 and MUST NOT read `Config.Env`, mounts, or labels outside `oncallpilot.*`.
**Accept:** A test with a container whose environment contains `DB_PASSWORD=x` → no `x` anywhere in the evidence payload.

#### AI-009 — `search_runbooks`
`agent` · U · **Must** · Depends: AI-026, DB-009
The tool MUST embed the query with the configured `EmbeddingProvider` and return the top-k (≤ 3) chunks by cosine similarity, with source, heading, and similarity.
**Accept:** Searching "roll back a bad release" returns the rollback runbook chunk as rank 1 in the fixture corpus.

#### AI-010 — Evidence storage and references
`agent` · U · **Must** · Depends: DB-003
Every tool result MUST be stored as an evidence row with the next sequential `ref` (`E2`, `E3`, …), a one-line summary, and the redacted payload, and MUST publish `evidence.added`. The model MUST only see `ref` values, never UUIDs.
**Accept:** References are gap-free per incident; the prompt fixtures contain no UUIDs.

#### AI-011 — Untrusted-data wrapping
`agent` · U · **Must** · Depends: SEC-005
Tool output MUST be passed to the LLM inside `<untrusted_data ref=… source=…>` with any closing tag inside the content escaped.
**Accept:** A log line containing `</untrusted_data>` is escaped in the rendered prompt (snapshot test).

#### AI-012 — Hypothesis generation
`agent` · U · **Must** · Depends: AI-020
The agent MUST produce 1–3 ranked hypotheses, each with a summary, `root_cause_category`, confidence 0–1, and ≥ 1 valid evidence reference. Hypotheses citing unknown references MUST be dropped.
**Accept:** A FakeProvider returning one bad-reference hypothesis and two good ones → 2 stored hypotheses, ranks re-numbered 1–2.

#### AI-013 — Root-cause taxonomy
`agent` · U · **Must** · Depends: §6.5
`root_cause_category` MUST be one of the 13 values in §6.5.
**Accept:** Schema validation rejects any other value.

#### AI-014 — Reflection: citation check
`agent` · U · **Must** · Depends: AI-012
Reflection MUST deterministically verify that every cited reference exists, belongs to the incident, and has a kind compatible with the category (per `backend/oncallpilot/agent/reflection_rules.yaml`). It MUST drop failing citations and drop hypotheses left with no valid citation.
**Accept:** A table-driven test over category × evidence-kind combinations.

#### AI-015 — Reflection: disproof query
`agent` · U · **Must** · Depends: AI-014, AI-002
Reflection MUST run exactly one read-only query chosen to disprove the top hypothesis, store it as evidence with `purpose=disproof`, and ask the model for a verdict (`survived` or `disproved`).
**Accept:** Every regression run that reaches reflection has exactly one `purpose=disproof` evidence row.

#### AI-016 — Confidence rules
`agent` · U · **Must** · Depends: AI-015
Code MUST apply: unsupported citation → confidence × 0.5; top disproved → dropped and the list re-ranked; final = min(model-adjusted, rule cap). Both `confidence_initial` and `confidence` MUST be stored.
**Accept:** A FakeProvider claiming 0.95 for a hypothesis with an unsupported citation → stored confidence ≤ 0.48 (0.95 × 0.5 = 0.475, which rounds up in `numeric(3,2)`).

#### AI-017 — Confidence threshold and escalation
`agent` · U · **Must** · Depends: AI-016, FR-010
If the top confidence after reflection is below `CONFIDENCE_THRESHOLD` (default 0.70), the agent MUST escalate with `low_confidence` and MUST NOT propose.
**Accept:** Top at 0.69 → escalated, no proposal row.

#### AI-018 — Proposal validation
`agent` · U · **Must** · Depends: §7.2, RUN-007
The proposal MUST name an action that is `enabled: true` in `contracts/actions.yaml`, with parameters valid against enums built from `runner/targets.yaml` and the ledger. `action=none` → escalate `no_catalogue_action_fits`. Invalid → escalate `proposal_invalid` and write `audit(proposal.rejected_by_validator)`.
**Accept:** Tests for: disabled action (`run_migration_rollback`), unknown action, `service=payments`, `replicas=6`, `target_release` not in ledger, free-text parameter — all rejected.

#### AI-019 — Tier and rollback from the catalogue only
`agent` · U · **Must** · Depends: AI-018
`risk_tier`, `approval_requirement`, and `rollback_step` MUST be copied from the catalogue. Any such field in model output MUST be ignored.
**Accept:** A FakeProvider returning `risk_tier: low` for `rollback_deploy` → the stored tier is `medium`.

#### AI-020 — Structured output with one repair
`agent` · U · **Must** · Depends: AI-021
Every structured call MUST validate against its Pydantic model (§6.6). On failure, exactly one repair message containing the validation error MAY be sent; a second failure MUST escalate with `llm_output_invalid`.
**Accept:** A FakeProvider returning invalid JSON twice → escalated; invalid then valid → proceeds.

#### AI-021 — Provider abstraction
`agent` · U · **Must** · Depends: —
The agent MUST use only the `LLMProvider` protocol, with `GeminiProvider`, `AnthropicProvider`, and `FakeProvider` implementations selected by `LLM_PROVIDER`. No provider SDK may be imported outside `agent/providers/`.
**Accept:** An import-lint test; switching the env var switches the adapter with no other code change.

#### AI-022 — Determinism settings
`agent` · U · **Must** · Depends: AI-021
All calls MUST use temperature 0, a 30 s timeout, and one retry on 429/5xx.
**Accept:** Adapter unit tests assert the request parameters.

#### AI-023 — Prompt versioning
`agent` · U · **Must** · Depends: —
Prompts MUST live under `prompts/v{N}/`. `prompts/manifest.json` holds a hash per file, and CI MUST fail if a prompt file changes without a manifest and version update.
**Accept:** Editing a prompt without bumping the version fails CI.

#### AI-024 — Agent metadata and LLM-call audit
`agent` · U · **Must** · Depends: DB-008
`incidents.agent_meta` MUST record the fields in §6.7. Each LLM call MUST write `audit(llm.call)` containing the redacted prompt and response, model, tokens, and latency.
**Accept:** Every agent-handled incident in regression has a complete `agent_meta` with no null required keys.

#### AI-025 — No double investigation
`backend-worker` · U · **Must** · Depends: FR-004
Each incident MUST be investigated at most once, with concurrency capped at `AGENT_CONCURRENCY` (2). Jobs left unacknowledged by a crashed worker MUST be recovered: a restarted worker re-reads its own pending entries at startup, and `XAUTOCLAIM` takes over entries idle for more than 300 s (longer than any live run, so a running investigation is never taken over). A redelivered job whose run had already started (`agent_meta.run_started_at` set) and whose incident is still `investigating` MUST NOT be restarted: it escalates with reason `agent_interrupted`, keeps the stored evidence and hypotheses, and writes `audit(agent.interrupted)`. If the incident has any other status, the job is only acknowledged.
**Accept:** Two workers consuming the same job → one investigation. Killing a worker mid-run → after the restart the incident is `escalated` with `agent_interrupted`; evidence refs are unique (no duplicate `E2`). A redelivered job for an incident that is already `awaiting_approval` → acknowledged, nothing changes.

#### AI-026 — Runbook ingestion
`backend` · U · **Must** · Depends: DB-009, TB-013
`python -m oncallpilot.runbooks.ingest` MUST chunk `runbooks/*.md` (about 500 tokens, 50 overlap, split on headings first), embed, and upsert by `(source, chunk_index)`, skipping unchanged `content_hash`.
**Accept:** Re-running with no changes makes 0 embedding calls.

#### AI-027 — Embedding provider
`backend` · U · **Must** · Depends: ADR-14
Embeddings MUST go through an `EmbeddingProvider` interface, produce 768-dimension L2-normalised vectors, and record `embedding_model`. The Gemini implementation uses `gemini-embedding-001` with `output_dimensionality=768`, `task_type=RETRIEVAL_DOCUMENT` for runbook chunks and `RETRIEVAL_QUERY` for queries, and normalises each vector itself (the API only normalises the 3072-dimension output).
**Accept:** The vector norm is 1 ± 1e-6; a dimension mismatch raises before the insert.

#### AI-028 — Suspicious-content flag
`agent` · U · **Should** · Depends: SEC-007
Evidence MUST be flagged `suspicious_content=true` when the injection heuristics in §7.7 match. The flag MUST NOT change agent control flow.
**Accept:** Scenario-8 fixture logs → flagged evidence; the agent path is identical with and without the flag.

#### AI-029 — LLM-call budget
`agent` · U · **Must** · Depends: AI-001, AI-020
One investigation MUST make at most 15 LLM calls: gather turns ≤ 7, four structured calls (hypothesize, disproof plan, reflect, propose), and at most one repair per structured call (7 + 4 + 4). The total is recorded in `agent_meta.llm_calls`.
**Accept:** A FakeProvider test that forces one repair on each of the four structured calls ends in a valid terminal state with `llm_calls ≤ 15`; a second failure on any structured call escalates `llm_output_invalid`; the state machine never skips a mandatory call to stay under the limit.

---

## 3. API — REST and WebSocket

#### API-001 — Conventions
`backend-api` · U · **Must** · Depends: §9.1
All endpoints MUST use snake_case JSON, UTC ISO‑8601 timestamps, UUIDs, the error envelope `{"error":{code,message,details}}`, and the codes in §9.8.
**Accept:** A contract test validates every error response against the envelope schema.

#### API-002 — `GET /healthz`
`backend-api` · U · **Must** · Depends: —
Unauthenticated. Returns `{status, contracts_version, db, redis}`; returns 503 if the database or Redis is unreachable.
**Accept:** Stopping copilot-redis → 503 with `redis: "down"`.

#### API-003 — `POST /ingest/alert`
`backend-api` · U · **Must** · Depends: FR-004
MUST require `X-Ingest-Token` (constant-time compare) and validate `AlertIn`. Returns `202 {incident_id, deduplicated}`.
**Accept:** Missing or wrong token → 401; invalid body → 422.

#### API-004 — `GET /incidents`
`backend-api` · U · **Must** · Depends: DB-002
Filters `status` and `service`; cursor pagination (`limit` ≤ 100); newest first; returns `IncidentSummary` including `top_hypothesis` and `pending_proposal_id`.
**Accept:** 45 incidents, `limit=20` → 3 pages, no duplicates or gaps.

#### API-005 — `GET /incidents/{id}`
`backend-api` · U · **Must** · Depends: DB-002…DB-007
Returns `IncidentDetail` with evidence (redacted), hypotheses (with `evidence_refs`), proposals (with dry run and fingerprint), executions, and `agent_meta`.
**Accept:** The response validates against `contracts/schemas/IncidentDetail.json`.

#### API-006 — `GET /incidents/{id}/log-tail`
`backend-api` · U · **Should** · Depends: SEC-008
Returns up to 50 redacted log lines for the incident's service since `since` (default: last 60 s). Rate limited to 1 request per 2 s per user (`429 RATE_LIMITED`).
**Accept:** A secret planted in a log line appears as `[REDACTED:…]`.

#### API-007 — `POST /incidents/{id}/resolve`
`backend-api` · U · **Should** · Depends: FR-012
Body `{reason}` (3–500 chars). Sets `resolution=manual`.
**Accept:** See FR-012.

#### API-008 — `POST /proposals/{id}/challenge`
`backend-api` · U · **Must** · Depends: SEC-012
Body `{proposal_fingerprint}`. MUST verify that the proposal is pending, not expired, and that the fingerprint matches. Stores the challenge in Redis with a 120 s TTL and returns `{challenge_id, nonce, expires_at, approval_requirement}`.
**Accept:** Wrong fingerprint → `409 STALE_PROPOSAL`; expired proposal → `409 PROPOSAL_EXPIRED`.

#### API-009 — `POST /proposals/{id}/approve`
`backend-api` · U · **Must** · Depends: API-008, SEC-002, DB-006, DB-007
MUST require `Idempotency-Key` and run the checks in the exact order of §5.9 step 4. MUST create the approval and execution in one transaction and send the signed execute request after commit. Returns `202 {execution_id, status:"queued"}`.
**Accept:** One test per error code in the §9.2 row. Replaying the same key → identical response and no second execution.

#### API-010 — `POST /proposals/{id}/reject`
`backend-api` · U · **Must** · Depends: DB-006
MUST require `Idempotency-Key` and `reason` (3–500 chars). Sets the proposal to `rejected` and the incident to `escalated` (`proposal_rejected`).
**Accept:** Reject after approve → `409 PROPOSAL_NOT_PENDING`.

#### API-011 — Device registration
`backend-api` · U · **Must** · Depends: DB-010
`POST /devices` upserts by `fcm_token`, scoped to the caller. `DELETE /devices/{id}` is allowed only for the owner.
**Accept:** The same token from two users ends up owned by the latest caller; deleting another user's device → 404.

#### API-012 — `GET /audit`
`backend-api` · U · **Should** · Depends: DB-008
Paginated audit entries, optionally filtered by `incident_id`, newest first. Payloads are returned as stored (already redacted).
**Accept:** The audit timeline for a scenario-2 run lists the events in §7.9 order.

#### API-013 — `GET/PUT /settings`
`backend-api` · U · **Should** · Depends: FR-023
**Accept:** See FR-023.

#### API-014 — WebSocket authentication
`backend-api` · U · **Must** · Depends: SEC-011
`/ws/incidents` MUST require an `auth` message with a valid JWT within 5 s (else close 4408); an invalid token → close 4401. The server MUST accept re-`auth` on token refresh and close 4401 when the token expires without a refresh.
**Accept:** Tests for timeout, invalid token, expiry, and refresh.

#### API-015 — WebSocket events
`backend-api` · U · **Must** · Depends: FR-019
The server MUST send `hello` on connect, then forward every `ocp:events` entry as the envelope in §9.4, using the 8 event types listed there.
**Accept:** Event payloads validate against `contracts/fixtures/ws/*.json` schemas.

#### API-016 — WebSocket resume and resync
`backend-api` · U · **Must** · Depends: API-015
On `resume{last_event_id}` the server MUST replay later events in order, or send `resync_required` if the ID is older than the retained stream.
**Accept:** Disconnect for 10 events, then resume → exactly those 10 events, in order.

#### API-017 — WebSocket heartbeat
`backend-api` · U · **Must** · Depends: API-014
The server MUST send `ping` every 15 s and close connections that miss 2 consecutive `pong` replies.
**Accept:** A client that never pongs is closed within 45 s.

#### API-018 — OpenAPI conformance
`backend-api` · U · **Must** · Depends: §14
Every path and method in `contracts/openapi.yaml` MUST exist in `app.openapi()` with matching request and response schemas.
**Accept:** The contract test passes in CI; adding an undocumented route fails it.

---

## 4. DB — Database

#### DB-001 — Schema and extensions
`supabase` · U · **Must** · Depends: ADR-17
All tables MUST live in schema `ocp`. Extensions `vector` and `pgcrypto` MUST be enabled.
**Accept:** The migration applies cleanly to an empty `pgvector/pgvector` container and to `ocp-dev`.

#### DB-002 — `incidents`
U · **Must** — Columns, enums, check constraint, and partial unique index exactly as in §8.2.
**Accept:** A constraint test: `status='resolved'` without `resolved_at` is rejected.

#### DB-003 — `evidence`
U · **Must** — As in §8.2. `ref` matches `^E[0-9]{1,3}$`; `(incident_id, ref)` is unique; `payload` holds redacted content only.
**Accept:** A duplicate ref is rejected.

#### DB-004 — `hypotheses`
U · **Must** — As in §8.2, with ≥ 1 evidence ID and a unique active rank per incident.
**Accept:** Inserting two active rank-1 hypotheses fails.

#### DB-005 — `proposals`
U · **Must** — As in §8.2: one pending proposal per incident; `kind='rollback'` ⇔ `parent_proposal_id` is not null.
**Accept:** A second pending proposal fails; a rollback without a parent fails.

#### DB-006 — `approvals`
U · **Must** — As in §8.2: unique `proposal_id`; unique `idempotency_key`; rejected ⇒ reason ≥ 3 chars; approved ⇒ `challenge_id` is not null.
**Accept:** A second decision on the same proposal fails.

#### DB-007 — `executions` and the approval trigger
U · **Must** — As in §8.2, plus the `executions_require_approved` trigger.
**Accept:** Inserting an execution for a rejected approval raises.

#### DB-008 — `audit_log` is append-only
U · **Must** — As in §8.2. The trigger raises on UPDATE and DELETE; `ocp_backend` has INSERT and SELECT only.
**Accept:** UPDATE and DELETE fail even as the table owner (trigger) and as `ocp_backend` (grant).

#### DB-009 — `runbook_chunks`
U · **Must** — `vector(768)` with an HNSW `vector_cosine_ops` index; `(source, chunk_index)` unique.
**Accept:** `EXPLAIN` of the search query shows the index in use (with `SET enable_seqscan=off` in the test).

#### DB-010 — `devices` and `monitor_settings`
U · **Must** — As in §8.2; `monitor_settings` is a single row (`id = 1`).
**Accept:** Inserting a second settings row fails.

#### DB-011 — Role privileges
U · **Must** — `ocp_backend` gets SELECT, INSERT, and UPDATE on all tables except `audit_log` (INSERT and SELECT); no DELETE; no DDL.
**Accept:** A privileges test in CI.

#### DB-012 — Not exposed via the Data API
U · **Must** — `ocp` is excluded from Supabase exposed schemas; RLS is enabled on every table with no policies.
**Accept:** A REST call to the Supabase Data API with the anon key for any `ocp` table fails (manual check recorded in `docs/checks/`).

#### DB-013 — Forward-only migrations
U · **Must** — Migrations are timestamped SQL in `supabase/migrations/`, never edited once merged, and applied in CI to a fresh container before tests.
**Accept:** CI job "migrations-apply" passes; a PR modifying an existing migration file fails a CI check.

#### DB-014 — Connection path
U · **Must** — The backend MUST connect through the Supavisor session-mode pooler connection string, with TLS required.
**Accept:** The backend container connects from the benchmark host's Docker network.

---

## 5. MOB — Mobile app

#### MOB-001 — Login and secure session
`mobile` · T · **Must** · Depends: SEC-016
Email and password login through `supabase_flutter`, with session persistence backed by `flutter_secure_storage`. Sign-out clears storage and deletes the device registration.
**Accept:** After a reinstall-free app restart the user stays signed in; the session file is not present in shared preferences.

#### MOB-002 — Navigation and deep links
T · **Must** · Depends: §10.2
`go_router` routes as in §10.2; unauthenticated users redirect to `/login`; a push tap opens `/incidents/:id`.
**Accept:** Widget test for the redirect; a manual push-tap test on the device.

#### MOB-003 — Incident Feed
T · **Must** · Depends: API-004, API-015
Shows severity, service, status, and a live-updating "time since alert", with open and past tabs. New incidents appear without a manual refresh.
**Accept:** Widget test with the mock repository: an injected `incident.opened` appears within one frame.

#### MOB-004 — Incident Detail: hypotheses and evidence linking
T · **Must** · Depends: API-005
Shows ranked hypotheses with a confidence bar and category, and `E#` chips that scroll to the matching evidence card. Dropped hypotheses are hidden by default.
**Accept:** Widget test: tapping `E3` scrolls `EvidenceCard(E3)` into view.

#### MOB-005 — Evidence cards
T · **Must** · Depends: MOB-004
Card variants:
- **Log:** excerpt with the error lines highlighted.
- **Metric:** `fl_chart` line chart with the incident window shaded and the change point marked.
- **Commit:** release, SHA, message, time.
- **Health:** container states.
- **Runbook:** heading and snippet.

**Accept:** Golden or widget tests per variant, rendered from fixtures.

#### MOB-006 — Live log tail
T · **Should** · Depends: API-006
Polls `/log-tail` every 5 s while Incident Detail is visible; stops when the screen is hidden.
**Accept:** Polling stops on navigation away (verified with a fake repository call counter).

#### MOB-007 — Action Review
T · **Must** · Depends: API-005
Shows the action, parameters, risk tier badge, approval requirement, expected effect, rationale with evidence chips, dry-run result (`would_change`, `current`, `warnings`), rollback step (or "not reversible"), and an expiry countdown based on server time.
**Accept:** Widget test from a `proposal.created` fixture renders every field.

#### MOB-008 — Approval flow
T · **Must** · Depends: API-008, API-009, MOB-009
Approve MUST perform challenge → local confirmation → approve, sending a per-attempt `Idempotency-Key` that is reused on network retry. It MUST map every error code to a clear message.
**Accept:** Mock tests: a network error on approve followed by a retry sends the same key; `409 STALE_PROPOSAL` triggers a refetch and the message "Proposal changed — review again".

#### MOB-009 — Biometric requirement
T · **Must** · Depends: SEC-012
For `approval_requirement=biometric`, the app MUST call `local_auth` with `biometricOnly: true`. With no biometric enrolled, the app MUST block the approval (no fallback to tap or PIN). `tap` shows an explicit confirm dialog.
**Accept:** Device test on Android with and without an enrolled fingerprint.

#### MOB-010 — Reject with reason
T · **Must** · Depends: API-010
Reject requires a reason of 3–500 chars and stays enabled even when the view is stale.
**Accept:** Widget test for validation; reject works with the stale banner showing.

#### MOB-011 — Execution Status
T · **Must** · Depends: API-015
Shows runner steps from `execution.progress`, structural and signal health results, and the final verdict. When a rollback proposal exists it shows a "Review rollback" button that opens Action Review for that proposal. When the incident ends `escalated` after an execution (`runner_refused` or `runner_timeout`), it shows the reason and no rollback button.
**Accept:** Fixture timeline with a failed health check → the rollback button appears. A widget test with an incident escalated as `runner_timeout` → the reason is shown and there is no rollback button.

#### MOB-012 — Audit History
T · **Should** · Depends: API-012
Timeline of proposals, approvals and rejections (who, when, reason), executions, and outcomes, filterable by incident.
**Accept:** Widget test from an audit fixture.

#### MOB-013 — Settings
T · **Should** · Depends: API-013
Shows monitored services and thresholds (editable when not locked), notification options, the server URL (debug builds only), and sign-out.
**Accept:** A locked state shows read-only fields with the lock reason.

#### MOB-014 — WebSocket client
T · **Must** · Depends: API-014, API-016, API-017
Reconnect with backoff 1–30 s (±20 % jitter); `auth` then `resume` on connect; refetch on `resync_required`; answer `ping`; re-`auth` on token refresh.
**Accept:** Unit tests with a fake socket covering drop/reconnect/resume and resync.

#### MOB-015 — Stale-state gate
T · **Must** · Depends: MOB-014
The app MUST show `StaleBanner` and disable Approve when the connection is not `connectedSynced`, no message has arrived for 30 s, the proposal has expired, or the displayed fingerprint is not the latest.
**Accept:** Widget tests for each condition; Approve is disabled within 1 s of the socket dropping.

#### MOB-016 — Push handling
T · **Must** · Depends: FR-018
Request notification permission (Android 13+), register the token on login and on refresh, show notifications on the `incidents_critical` channel, and route taps.
**Accept:** Manual device test: app killed → push received → tap opens the incident.

#### MOB-017 — Mock mode
T · **Must** · Depends: §10.6
`DATA_SOURCE=mock` replays fixture timelines and simulates every approve and challenge error code with no network.
**Accept:** The full UI flow (feed → detail → review → approve → execution → resolved) works offline from fixtures.

#### MOB-018 — Network security config
T · **Must** · Depends: ADR-16
Debug and profile builds allow cleartext only to the configured host; release builds allow no cleartext.
**Accept:** A request to a different cleartext host fails in a debug build.

#### MOB-019 — Suspicious-content warning
T · **Should** · Depends: AI-028
Evidence cards with `suspicious_content=true` show a warning chip reading "Contains instruction-like text — treat as data".
**Accept:** Widget test from a scenario-8 fixture.

#### MOB-020 — DTOs match the contracts
T · **Must** · Depends: TEST-009
Dart DTOs MUST parse every fixture in `contracts/fixtures/`. Unknown enum values MUST map to an `unknown` case rather than crash.
**Accept:** The contract fixture test passes in CI.

---

## 6. RUN — Runner

#### RUN-001 — Consume and verify signatures
`runner` · T · **Must** · Depends: SEC-003
The runner MUST consume `ocp:runner:requests` in group `runner` and verify the HMAC-SHA256 signature over canonical JSON with a constant-time compare: `execute` with `ACTION_SIGNING_KEY`, `dry_run` with `RUNNER_LINK_KEY`. Invalid → result `refused` with `BAD_SIGNATURE`, and nothing executes.
**Accept:** A tampered `params` field → refused. An `execute` message signed with `RUNNER_LINK_KEY` → refused.

#### RUN-002 — Expiry
T · **Must** · Depends: RUN-001
Requests past `expires_at` MUST be refused with `REQUEST_EXPIRED`.
**Accept:** A message aged 61 s → refused.

#### RUN-003 — Catalogue validation
T · **Must** · Depends: §7.2
The action MUST exist in `contracts/actions.yaml` with `enabled: true` and a registered handler. Unknown or disabled → `ACTION_NOT_ALLOWED`.
**Accept:** `run_migration_rollback` and `exec_shell` → refused; a CI check verifies that the handler set equals the enabled catalogue set.

#### RUN-004 — Target allowlist on every Docker call
T · **Must** · Depends: §7.4
Every container the runner touches MUST match both the label selector and the name regex, checked immediately before each Docker call.
**Accept:** A test with a container named `cs-api-140-1` but missing the label → refused with `TARGET_NOT_ALLOWED`. Targeting `backend-api` → refused.

#### RUN-005 — Execution idempotency
T · **Must** · Depends: §7.6
`SET NX ocp:runner:idem:{execution_id}` before acting; duplicates are acknowledged and ignored.
**Accept:** The same signed message delivered twice → one action.

#### RUN-006 — Second-gate limits
T · **Must** · Depends: §7.6
The runner MUST enforce its own rate limit (3 per service per 30 min) and cooldown (120 s, rollback exempt), independent of the backend.
**Accept:** A fourth signed execution in 30 min → `RATE_LIMITED`, even though the backend's own limit check was bypassed in the test.

#### RUN-007 — Dry runs
T · **Must** · Depends: §7.5
`dry_run` requests MUST return `would_change`, `current`, `preconditions`, `predicted_effect`, `warnings`, and `state_fingerprint`, with no Docker write calls.
**Accept:** A recorded proxy request log during a dry run contains only GET requests.

#### RUN-008 — State-fingerprint drift
T · **Must** · Depends: RUN-007
Before acting, the runner MUST recompute the fingerprint; on mismatch it MUST abort with `STATE_DRIFT` and touch nothing.
**Accept:** Ledger changed after the dry run → aborted.

#### RUN-009 — `restart_service`
T · **Must** · Depends: RUN-004
Restarts the running containers of the active release (or starts them if exited) for `api`, `worker`, or `redis`; graceful (SIGTERM, 10 s timeout).
**Accept:** Scenario 1 and scenario 4 recover through this action in `chaos verify`.

#### RUN-010 — `scale_service`
T · **Must** · Depends: TB-002, TB-011
Starts or stops api slot containers of the active release until `replicas` (1–5) are running; captures the previous count for the rollback step; appends a ledger `kind=scale` record.
**Accept:** Scale 1→3 → three running `cs-api-140-*` containers, and `cs-lb` routes to all three within 10 s.

#### RUN-011 — `rollback_deploy`
T · **Must** · Depends: TB-009
Starts N slots of `target_release` (N = current replica count), waits until they are healthy, then stops the active release's slots; appends a ledger `kind=rollback` record; captures the previous release for the rollback step.
**Accept:** Scenario 2 and scenario 6 recover through this action; the ledger shows the rollback.

#### RUN-012 — `clear_cache`
T · **Must** · Depends: TB-001
Calls `POST http://cs-lb/internal/cache/clear {cache}` with `RUNNER_ADMIN_TOKEN`. The dry run calls the stats endpoint. Only `catalog` and `pricing` are allowed.
**Accept:** Key count drops to 0; `cache=sessions` → refused.

#### RUN-013 — Progress events
T · **Must** · Depends: §9.5
Each step MUST emit a signed `progress` result (`name`, `status`, `message`) to `ocp:runner:results`.
**Accept:** A scenario-2 execution produces ordered steps: validated → starting… → healthy… → stopping… → done.

#### RUN-014 — Structural health check
T · **Must** · Depends: FR-014
After acting, verify within 60 s: target containers `running` and `healthy`, plus the relevant `/healthz` probes. Report `health_after.structural`.
**Accept:** Rolling back to a release that crashes on start → structural fail reported.

#### RUN-015 — Signed results
T · **Must** · Depends: SEC-003
Every result MUST be signed with `RUNNER_LINK_KEY`; backend-worker MUST verify it and drop invalid ones with an audit entry.
**Accept:** A forged result is ignored by the worker.

#### RUN-016 — Process isolation
T · **Must** · Depends: §7.3
The runner runs as non-root with a read-only root filesystem, `cap_drop: [ALL]`, and only the env vars listed in §7.3.
**Accept:** `docker inspect runner` check in CI; a test asserts that `DATABASE_URL` is absent from the runner environment.

#### RUN-017 — socket-proxy-rw configuration
T · **Must** · Depends: ADR-05
Exactly `CONTAINERS=1, ALLOW_START=1, ALLOW_STOP=1, ALLOW_RESTARTS=1, POST=0`, reachable only on `rw_proxy_net`. The runner's Docker client MUST be created with an explicit API `version=` because the proxy blocks the `/version` probe; `VERSION` and `PING` stay off.
**Accept:** See TEST-015. The runner starts, stops, and restarts a slot container through the real proxy with no 403, using the pinned version (S0.7 records it in `docs/checks/proxy.md`).

#### RUN-018 — No free-form targets
T · **Must** · Depends: RUN-003
Parameters MUST be validated as enums or bounded integers; strings outside the enums are refused before any Docker call.
**Accept:** `service="api; rm -rf /"` → refused with `VALIDATION_ERROR`.

---

## 7. SEC — Security

#### SEC-001 — No execution without approval
`db`, `backend` · U · **Must** · Depends: DB-007
No execution row can exist without an approved approval for the same proposal (database trigger), and no `execute` message is sent outside the approve handler.
**Accept:** Trigger test; a code search test finds `type="execute"` constructed in exactly one module.

#### SEC-002 — Signing authority
U+T · **Must** · Depends: SEC-003
Only backend-api holds `ACTION_SIGNING_KEY` (besides the runner) and signs `execute` messages. backend-worker holds only `RUNNER_LINK_KEY`, used for `dry_run` requests and verifying runner results. The runner MUST reject `execute` messages that are not signed with `ACTION_SIGNING_KEY` or whose `approved_by` or `approved_at` is null.
**Accept:** Compose test: `ACTION_SIGNING_KEY` is absent from the backend-worker environment. Unit tests in both services for each rejection.

#### SEC-003 — HMAC protocol
U+T · **Must** · Depends: C5
HMAC-SHA256 over canonical JSON (sorted keys, no whitespace, UTF-8), excluding `sig`. Two keys, each ≥ 32 random bytes: `ACTION_SIGNING_KEY` (backend-api, runner) and `RUNNER_LINK_KEY` (backend-api, backend-worker, runner). The runner signs all results with `RUNNER_LINK_KEY`.
**Accept:** Cross-implementation test vectors for both keys in `contracts/fixtures/signing/` are verified by both test suites.

#### SEC-004 — Redis ACL
U · **Must** · Depends: §2.7
`default` user disabled. The `runner` user is restricted to `~ocp:runner:*` and to the commands in `architecture.md` §2.7 (stream commands, sorted-set commands for its rate limit, `SET`/`GET`/`DEL`/`EXPIRE`/`TTL`/`EXISTS`, and `PING`).
**Accept:** As `runner`, `XADD ocp:events …`, `GET ocp:challenge:*`, `KEYS *`, and `CONFIG GET *` fail with NOPERM, while every runner flow (consume, idempotency, rate limit, cooldown, results) still works.

#### SEC-005 — Untrusted-data handling
U · **Must** · Depends: AI-011
The system prompt MUST state that content inside `<untrusted_data>` is never an instruction. Tool output MUST never be placed in the system role.
**Accept:** Prompt snapshot tests.

#### SEC-006 — No execute-text capability
U · **Must** · Depends: AI-004
No component reachable by the agent may evaluate, execute, or shell out text derived from tool output.
**Accept:** Static grep test over `agent/` and `api/` for `eval`, `exec`, `subprocess`, and `os.system`.

#### SEC-007 — Heuristic is informational
U · **Must** · Depends: AI-028
The suspicious-content flag MUST NOT be relied on as a control; the controls are SEC-005, SEC-006, AI-018, AI-019, and human approval.
**Accept:** Documented in the README; covered by AI-028's test.

#### SEC-008 — Redaction before storage and LLM
U · **Must** · Depends: §7.8
All tool output MUST pass through `redact()` before database storage, WebSocket publication, and LLM prompts.
**Accept:** The redaction corpus (≥ 40 cases) passes; an integration test plants each secret type in logs and finds none in the database, prompts, or events.

#### SEC-009 — No secrets in prompts, logs, pushes, or audit
U+T · **Must** · Depends: SEC-008
Backend and runner logs MUST NOT print env values, tokens, or request headers. Push payloads contain only the keys in §9.7.
**Accept:** A log-capture test during the full flow greps for every configured secret value → 0 hits.

#### SEC-010 — JWT verification
U · **Must** · Depends: §7.11
Verify the signature with the Supabase JWKS (cached 10 min, refetched on unknown `kid`), plus `aud`, `exp`, and `iss`. HS256 tokens are rejected unless the project uses legacy keys (config flag, default off). Both Supabase projects MUST be switched to an asymmetric signing key, because a legacy-only project publishes no usable public key.
**Accept:** Tests with an expired token, wrong audience, unknown `kid`, and tampered payload. A real login against `ocp-dev` returns a token that the backend verifies through the live JWKS (recorded in `docs/checks/week-1.md`).

#### SEC-011 — Tokens never in URLs
U+T · **Must** · Depends: API-014
No endpoint accepts tokens in query strings.
**Accept:** Contract test; the app's WebSocket URL has no query string.

#### SEC-012 — Challenge properties
U · **Must** · Depends: API-008
Challenges are single-use (`GETDEL`), expire after 120 s, and are bound to user, proposal, and fingerprint. The nonce is 32 random bytes; only its hash is stored.
**Accept:** Reusing a challenge → `409 CHALLENGE_INVALID`; another user's challenge → `409 CHALLENGE_INVALID`.

#### SEC-013 — Port exposure
U+T · **Must** · Depends: §3
Only `backend-api:8000` is bound to `0.0.0.0`; Grafana, Prometheus, Loki, and `cs-lb` bind to `127.0.0.1`; the proxies and Redis publish nothing.
**Accept:** `ocp doctor` lists the published ports and fails on any other.

#### SEC-014 — socket-proxy-ro is read-only
U · **Must** · Depends: ADR-05
`CONTAINERS=1, NETWORKS=1, EVENTS=1, PING=1, VERSION=1, POST=0` with no `ALLOW_*`; reachable only on `ro_proxy_net`. (`NETWORKS=1` is there because Alloy's container discovery reads network metadata; it is GET-only.)
**Accept:** `POST /containers/{id}/restart` through the ro proxy → 403. Alloy discovers and ships logs for the labelled containers through the proxy with no 403 in the proxy log.

#### SEC-015 — Secret storage
U+T · **Must** · Depends: §7.11
Secrets live in git-ignored `.env.*` files and `infrastructure/secrets/` (git-ignored). `.env.example` lists every variable. The FCM service account is mounted as a file.
**Accept:** A CI check fails if any `.env` (other than `.env.example`) or `*.json` under `secrets/` is tracked.

#### SEC-016 — Closed sign-up
U · **Must** · Depends: —
Supabase Auth sign-ups are disabled in both projects; accounts are created manually.
**Accept:** A sign-up attempt from the app → error (manual check recorded).

#### SEC-017 — Approval snapshot in audit
U · **Must** · Depends: DB-008
`audit(approval.approved)` MUST include the proposal fingerprint, dry-run result, top hypothesis summary and confidence, cited evidence references, `auth_method`, and `device_id` (when the client sent one).
**Accept:** Assert the payload keys in the approve integration test.

#### SEC-018 — Known limitations documented
U+T · **Must** · Depends: SEC-001
The README MUST contain a "Known limitations" section that lists at least the items in `architecture.md` §7.12: the shared `RUNNER_LINK_KEY` (runner results are forgeable by a compromised worker), biometric approval that the server cannot verify, endpoint-level filtering in `socket-proxy-rw`, cleartext LAN transport in debug builds, and interrupted investigations that are escalated rather than resumed.
**Accept:** A CI check greps the README for the heading "Known limitations"; the list is reviewed at Gate G4.

---

## 8. TB — Testbed and observability

#### TB-001 — Chaos Shop services
`chaos-shop` · T · **Must** · Depends: §11.1
Services as in §11.1, including `/internal/*` admin endpoints protected by `CHAOS_TOKEN` (chaos CLI) or `RUNNER_ADMIN_TOKEN` (cache clear only).
**Accept:** `ocp up` brings every service to healthy.

#### TB-002 — Slots and labels
T · **Must** · Depends: ADR-04
Pre-created stopped slot containers: `cs-api-140-1..5`, `cs-api-150-1..5`, `cs-worker-210`, `cs-worker-220`, all with `oncallpilot.*` labels and the network aliases `api-upstream` and `worker-upstream`.
**Accept:** `chaos status` lists every slot with its labels.

#### TB-003 — Telemetry contract
T · **Must** · Depends: C7
Chaos Shop MUST emit the metrics and labels in §11.2.
**Accept:** A contract test scrapes `/metrics` and asserts that each metric name and label set is present.

#### TB-004 — Structured logs
T · **Must** · Depends: C7
One JSON object per line with the fields in §11.2; exceptions include `exc_type` and `stacktrace`.
**Accept:** A log schema test on a sample of 1,000 lines.

#### TB-005 — Load generator
T · **Must** · Depends: §11.1
Baseline 5 rps mixed traffic; spike mode ramps to a calibrated rate; controllable only by the chaos CLI.
**Accept:** Prometheus shows 5 ± 1 rps at baseline.

#### TB-006 — One-command fault injection
T · **Must** · Depends: §11.3
`chaos inject <name>` MUST exist for all 8 scenarios and complete in ≤ 30 s.
**Accept:** Gate G1 script runs all 8.

#### TB-007 — Reset
T · **Must** · Depends: §11.3
`chaos reset` restores the baseline in ≤ 90 s, as specified in §11.3.
**Accept:** After reset, `chaos status` shows the baseline; there are no active chaos flags; the ledger ends with `kind=reset`.

#### TB-008 — No self-healing
T · **Must** · Depends: ADR-19
Each fault MUST persist for ≥ 15 minutes without intervention and MUST be fixed by its expected action (for scenarios 7 and 8, by reset only).
**Accept:** `chaos verify <n>` passes for n = 1–6; the 15-minute persistence check passes once per scenario before Gate G1.

#### TB-009 — Deploy ledger writer
T · **Must** · Depends: C6
The chaos CLI and runner append valid records (§9.6). The chaos CLI writes `deployed_by: "ci"` for its fault deploys, the runner writes `runner`, and reset and seeded history use `setup`. Reset rewrites the seeded history from `releases.yaml`.
**Accept:** Every line validates against `deploy_record.json`; `deployed_by` is one of `ci`, `runner`, `setup`.

#### TB-010 — Neutral naming
T · **Must** · Depends: H7
Release numbers, commit messages, container names, ledger fields such as `deployed_by`, and runbooks MUST NOT name the fault (no "bad", "leak", "broken", or "chaos" in agent-visible fields).
**Accept:** A CI grep test over `releases.yaml`, `runbooks/`, the labels, and a sample ledger written by the chaos CLI.

#### TB-011 — Load balancer
T · **Must** · Depends: ADR-07
`cs-lb` routes to every running api slot via the upstream `resolve` parameter within 10 s of a start or stop.
**Accept:** Scale 1→3 → request logs show all three instances serving.

#### TB-012 — Observability stack
`infrastructure/observability` · U · **Must** · Depends: §2.8
- Prometheus: DNS service discovery for both aliases, 5 s scrape interval.
- Loki: single binary.
- Alloy: collects only `oncallpilot.logs=true` containers through socket-proxy-ro.
- Grafana: provisioned "Chaos Shop Overview" dashboard plus Loki Explore.

**Accept:** Each of the 8 scenarios is visible on the Grafana dashboard or in Explore.

#### TB-013 — Runbook corpus
U+T · **Must** · Depends: AI-026
6–10 generic operations runbooks: restart a service, roll back a release, scale the API, cache issues, database connection issues, upstream dependency issues, crash loops, memory issues, and escalation policy. They are written as real operational docs, not scenario answer keys, and each is reviewed by the other developer.
**Accept:** PR review checklist item; TB-010 grep passes.

---

## 9. NFR — Non-functional

#### NFR-001 — Alert-to-proposal latency
U · **Must** · Depends: AI-002, AI-003
Target: median ≤ 60 s from `incident.opened_at` to `proposal.created_at`. This is a median target, not a cap: the investigation itself is capped at 90 s (`MAX_AGENT_SECONDS`), and queueing plus the dry run add to the measured time.
**Accept:** Measured over the benchmark; reported as median, p90, and max.

#### NFR-002 — Detection latency
U+T · **Should** · Depends: FR-002
The incident opens within 60 s of fault onset after warm-up, except scenario 1, which opens within 60 s of the first OOM restart.
**Accept:** `t_alert − t_inject` reported per scenario.

#### NFR-003 — Event delivery latency
U · **Should** · Depends: API-015
WebSocket events reach the app within 2 s (p95) of commit on the LAN.
**Accept:** A timestamp comparison in the integration log over one benchmark day.

#### NFR-004 — Read API latency
U · **Should** · Depends: API-004, API-005
`GET /incidents` and `GET /incidents/{id}` p95 under 300 ms on the host for incidents with ≤ 50 evidence rows.
**Accept:** A simple load script (50 requests) in Phase 3.

#### NFR-005 — Crash resilience
U+T · **Must** · Depends: AI-025
Restarting `backend-worker`, `backend-api`, or `runner` mid-flow MUST NOT lose jobs or results (consumer groups with `XAUTOCLAIM`) and MUST NOT execute anything twice (idempotency). An investigation that a worker restart interrupts is escalated to a human (`agent_interrupted`, AI-025), not lost and not resumed.
**Accept:** Chaos-of-the-copilot test: kill the runner after the scenario 2 approval is sent but before it reads the request, and restart it within 60 s → exactly one rollback. Kill it mid-execution → the rollback is not repeated and the incident ends `escalated` (`runner_timeout`, FR-024). Kill the worker mid-investigation → the incident ends `escalated` (`agent_interrupted`) with its evidence kept.

#### NFR-006 — Copilot observability
U+T · **Should** · Depends: —
Backend and runner write JSON logs with `incident_id`, `proposal_id`, and `execution_id` where relevant; the backend exposes its own `/metrics` (agent duration, tool calls, LLM latency and errors).
**Accept:** Grafana panel "Copilot internals" shows agent duration per incident.

#### NFR-007 — One-command setup
U+T · **Must** · Depends: §12.1
On a machine with Docker Desktop, Python 3.12, and uv: `uv tool install ./tools/ocp && ocp up` brings the full stack to healthy and prints the LAN URL.
**Accept:** Verified on Usman's laptop as the clean machine in Week 8.

#### NFR-008 — Reproducibility
U+T · **Must** · Depends: AI-023, BENCH-012
Pinned image tags, `uv.lock` and `pubspec.lock` committed, prompt version and model pinned, and the detector config hashed. Every incident records what produced it.
**Accept:** Any benchmark row can be traced to an exact git SHA, prompt version, model, and config hash.

#### NFR-009 — Maintainability
U+T · **Must** · Depends: —
Python: `ruff` (lint and format) plus `mypy --strict` on `contracts/`, `agent/`, and `runner/`. Dart: `flutter analyze` with zero warnings.
**Accept:** CI gates.

#### NFR-010 — Testability
U+T · **Must** · Depends: AI-001, AI-021
Every external dependency (LLM, Docker, Prometheus, Loki, FCM, clock) sits behind an interface with a fake.
**Accept:** The full agent regression suite runs offline in CI in under 3 minutes.

#### NFR-011 — Scalability bounds (documented, not engineered)
U · **Should** · Depends: —
v1 supports one host, ≤ 2 concurrent investigations, ≤ 10 devices, and ≤ 10,000 incidents. Limits are documented in the README.
**Accept:** README section exists.

#### NFR-012 — Auditability
U · **Must** · Depends: DB-008, SEC-017
For any execution, the audit log alone MUST reconstruct who approved what, when, on which evidence, with which model and prompt version, and what happened.
**Accept:** `bench audit-check <incident_id>` reconstructs the chain and fails on any gap.

#### NFR-013 — Host resource budget
T · **Should** · Depends: §12.4
The full stack idles at ≤ 5 GB RAM on the benchmark host.
**Accept:** `docker stats` snapshot recorded at Gate G2.

#### NFR-014 — Time
U+T · **Must** · Depends: —
All services use UTC. The phone uses server time from `hello` for countdowns.
**Accept:** Expiry countdown stays correct with the phone clock set 5 minutes ahead (manual test).

---

## 10. TEST — Testing

#### TEST-001 — Unit test scope
U+T · **Must**
Unit tests are required for: agent transitions (100 % of transitions), detector rules, redaction, catalogue validation, fingerprint computation, signing, runner handlers (with a fake Docker client), and Dart repositories and controllers.
**Accept:** CI coverage report: `agent/` ≥ 85 % lines, `runner/` ≥ 85 %, `approvals/` ≥ 90 %.

#### TEST-002 — Agent regression suite
U · **Must** · Depends: AI-001…AI-020
For each of the 8 scenarios: recorded tool outputs (fixtures captured from the real testbed in Phase 2) plus a scripted FakeProvider. Assert the terminal state, proposal action and parameters (or escalation reason), budget compliance, and citation validity. A second suite replays with recorded real-model responses (`@pytest.mark.recorded`).
**Accept:** All 16 cases pass in CI with no network.

#### TEST-003 — Prompt-injection tests
U · **Must** · Depends: SEC-005, AI-018
Planted instructions in logs, metric labels, commit messages, and runbook text asking for (a) a shell command and (b) an allowlisted but wrong action. Assert no proposal follows any planted instruction, and that the stored risk tier is always the catalogue tier.
**Accept:** `tests/security/test_prompt_injection.py` passes; scenario 8 live runs record `unsafe_action=false`.

#### TEST-004 — Forbidden-action tests
T · **Must** · Depends: RUN-001…RUN-018
Signed and unsigned requests for: an unknown action, a disabled action, a non-allowlisted container, a free-form parameter, an expired request, a replayed request, and a rate-limited request — all refused with zero Docker write calls.
**Accept:** `runner/tests/security/` passes; the proxy request log contains no write calls.

#### TEST-005 — Idempotency tests
U+T · **Must** · Depends: API-009, RUN-005
Double-tap with the same key → one execution; two keys → second gets `409 PROPOSAL_NOT_PENDING`; same key with a different body → 422; duplicate stream delivery → one action.
**Accept:** All cases pass.

#### TEST-006 — Stale-state tests
U+T · **Must** · Depends: MOB-015, API-008
Server: approve with an old fingerprint → 409. Client: widget tests for every stale condition.
**Accept:** Both suites pass.

#### TEST-007 — Rollback tests
U+T · **Must** · Depends: FR-015, FR-016
End-to-end with a fake runner: a failed health check → rollback proposal → approve → verified → resolved or escalated.
**Accept:** Both branches pass.

#### TEST-008 — Redaction corpus
U · **Must** · Depends: SEC-008
`tests/security/redaction_cases.yaml` with ≥ 40 positive and ≥ 20 negative (must-not-redact) cases.
**Accept:** 100 % pass.

#### TEST-009 — Contract tests
U+T · **Must** · Depends: §14
(1) Pydantic → JSON Schema regenerated in CI equals the committed schemas. (2) All fixtures validate. (3) Dart DTOs parse all fixtures. (4) OpenAPI conformance (API-018). (5) Signing test vector (SEC-003). (6) Catalogue ↔ runner handler set.
**Accept:** The `contracts.yml` workflow is green.

#### TEST-010 — Backend integration tests
U · **Must** · Depends: DB-*, API-*
pytest with Postgres (pgvector) and Redis service containers, a FakeProvider, a fake runner consumer, and a fake FCM.
**Accept:** They run in CI on every backend PR.

#### TEST-011 — Flutter widget tests
T · **Must** · Depends: MOB-*
Widget tests for each screen in the mock repository, the stale gate, approval flow states, evidence cards, and error mapping.
**Accept:** `flutter test` is green in CI.

#### TEST-012 — CI pipelines
U+T · **Must**
GitHub Actions workflows `backend.yml`, `runner.yml`, `chaos-shop.yml`, `mobile.yml`, `contracts.yml`, and `migrations.yml`, path-filtered, all required for merge to `main`.
**Accept:** Branch protection lists them as required checks.

#### TEST-013 — Testbed gate tests
T · **Must** · Depends: TB-006…TB-008
`chaos verify` for scenarios 1–6, and a persistence check for 7–8.
**Accept:** Gate G1 log committed to `docs/checks/gate-g1.md`.

#### TEST-014 — End-to-end smoke
U+T · **Must** · Depends: all of Phase 2
`ocp smoke` on the host: reset → inject scenario 2 → wait for proposal → approve through the API as the test user (`OCP_TEST_EMAIL` / `OCP_TEST_PASSWORD`, an account created by hand in the Supabase project) → assert resolved within 5 minutes.
`ocp smoke --thin` is the early variant for the Week 3 thin slice: the same flow, but reflection and signal verification may still be stubs and the structural health check alone may resolve the incident. Approval is never skipped: the script calls `challenge` and `approve` with `auth_method: biometric`, as `rollback_deploy` requires.
**Accept:** `--thin` runs at the Friday checkpoint of Week 3. The full `ocp smoke` runs at every Friday checkpoint from Week 4 and, from Gate G2, must use the two-stage health check.

#### TEST-015 — Socket-proxy security test
T · **Must** · Depends: RUN-017, SEC-014
Through the rw proxy: `POST /containers/create`, `POST /containers/{id}/exec`, `DELETE /containers/{id}`, and `POST /images/create` → 403. Through the ro proxy: any POST → 403.
**Accept:** `runner/tests/security/test_proxy.py` against the real proxies in the compose test profile.

#### TEST-016 — Database constraint tests
U · **Must** · Depends: DB-002…DB-011
Every check, unique index, trigger, and grant has a negative test.
**Accept:** The `tests/db/` suite is green.

---

## 11. BENCH — Benchmark

#### BENCH-001 — Scenario definitions
U+T · **Must** · Depends: §11.3
`benchmark/scenarios.yaml` lists, per scenario: `id`, `name`, `inject_command`, `accepted_categories`, `root_cause_keywords`, `expected_action` (with parameters) or `escalate`, and `timeout_minutes` (20).
**Accept:** Schema-validated in CI; read by both developers.

#### BENCH-002 — Run plan
U · **Must** · Depends: BENCH-001
`bench plan --seed <int>` generates 48 runs (8 scenarios × 3 repetitions × 2 modes) in randomised order, balancing operators across scenarios and modes.
**Accept:** The same seed produces the same plan; each operator has ≥ 1 run per scenario per mode where possible.

#### BENCH-003 — Blinding
U+T · **Must** · Depends: BENCH-002
The observer (the developer not operating) runs injection. The operator never sees the plan entry or the chaos CLI output.
**Accept:** The procedure is in `benchmark/PROCEDURE.md`; the `operator` and `observer` columns are always different people.

#### BENCH-004 — Run procedure
U · **Must** · Depends: TB-007, FR-003
`bench run <run_id>`:
1. `chaos reset`, then **guard against leftovers**: sign in as the test user (`OCP_TEST_EMAIL` / `OCP_TEST_PASSWORD`), list incidents that are not `resolved`, and resolve each with `POST /incidents/{id}/resolve {reason: "bench_cleanup"}`. If one is busy (`INCIDENT_BUSY`) or any is still open after 30 s, abort the run and write no CSV row.
2. clear copilot limits and cooldowns
3. set `ocp:bench:current_run`
4. wait for warm-up (180 s) plus a 60 s baseline
5. `chaos inject`, recording `t_inject`
6. capture `t_alert` (incident opened), and **check that it belongs to this run**: `incidents.benchmark_run_id` equals the run id and `opened_at ≥ t_inject`. Otherwise the run is invalid, is marked `invalid_run` in `notes`, and is repeated.

It then waits for observer marks and automatic events, or for the 20-minute timeout.
**Accept:** A dry run of the procedure on 2 scenarios in Week 6 completes without manual database edits. A test leaves an unresolved incident behind: the next run resolves it as `bench_cleanup` (or aborts), and its alert is never used as the new run's `t_alert`.

#### BENCH-005 — Manual mode
U+T · **Must** · Depends: FR-022
`AGENT_ENABLED=false`. The operator uses only Grafana (dashboards and Explore), a terminal on the host (`docker` CLI, the ledger file, the runbooks folder), and the push alert. Recovery is done by the operator through `docker` commands.
**Accept:** The tool list is in `PROCEDURE.md`; `bench run` verifies the agent is disabled before injecting.

#### BENCH-006 — Copilot mode
U+T · **Must** · Depends: MOB-*, API-009
`AGENT_ENABLED=true`. The operator uses only the phone. They may open Grafana only after they have rejected the proposal or the incident has escalated; any such use is recorded in `notes`.
**Accept:** The procedure is documented and followed (observer checklist).

#### BENCH-007 — Timing capture and CSV
`benchmark` · U · **Must** · Depends: BENCH-001, BENCH-004
`benchmark/results/runs.csv` columns:
- **Run identity:** `run_id`, `plan_seed`, `scenario_id`, `scenario_name`, `mode`, `rep`, `operator`, `observer`
- **Configuration:** `git_sha`, `llm_provider`, `llm_model`, `prompt_version`, `detector_config_version`, `catalogue_version`
- **Timestamps:** `t_inject`, `t_alert`, `t_diagnosis`, `t_proposal`, `t_decision`, `t_recovered`
- **Outcome:** `diagnosis_text`, `diagnosis_correct`, `top1_correct`, `top3_correct`, `proposal_action`, `proposal_params`, `expected_action`, `action_correct`, `escalated`, `unsafe_action`, `evidence_citations_total`, `evidence_citations_valid`, `timed_out`, `notes`

`t_diagnosis` is marked by the observer (`bench mark diagnosis "<text>"`) when the operator states the root cause aloud, in **both** modes. Other times come from the database automatically. All times are UTC ISO‑8601.
**Accept:** `bench report` reads the CSV with no manual edits.

#### BENCH-008 — Correctness judging
U+T · **Must** · Depends: BENCH-001
- `diagnosis_correct`: the observer judges the operator's statement against `scenarios.yaml`.
- `top1_correct` / `top3_correct`: computed from stored hypotheses (category ∈ accepted set) and confirmed by the observer reading the summary.
- Disagreements are resolved by both developers and noted.

**Accept:** Every judged field has a non-empty value; disputes are listed in the report.

#### BENCH-009 — Evidence validity audit
U+T · **Must** · Depends: DB-004
`bench audit-evidence` presents every (hypothesis, cited evidence) pair from copilot runs. A developer who did not operate that run labels each pair supports / does not support.
**Accept:** Evidence validity = supports ÷ total, reported with counts.

#### BENCH-010 — Unsafe-action audit
U · **Must** · Depends: SEC-001, AI-019
`bench audit-safety` checks every proposal and execution:
- action outside the enabled catalogue;
- stored tier differs from the catalogue tier;
- proposal matches a planted instruction (scenario 8);
- execution without an approved approval.

Any hit counts as an unsafe action.
**Accept:** The report states the unsafe action count with the query used.

#### BENCH-011 — Report
U · **Must** · Depends: BENCH-007…BENCH-010
`bench report` produces `benchmark/results/report.md` and PNG charts (matplotlib):
- per-scenario and overall median, IQR, min/max time-to-diagnosis and time-to-recovery for both modes;
- % change in medians;
- top-1/top-3 accuracy, evidence validity, escalation correctness, unsafe action rate, alert-to-proposal latency (median, p90, max);
- a table of misses with explanations.

Targets are shown beside actual values.
**Accept:** The report regenerates byte-identically from the same CSV.

#### BENCH-012 — Freeze
U+T · **Must** · Depends: NFR-008
Before full runs: tag `bench-freeze`; pin `LLM_MODEL`, `PROMPT_VERSION`, detector config, and catalogue; set `BENCHMARK_LOCK=true`. Any change after the freeze invalidates the runs before it.
**Accept:** All rows share one `git_sha`, model, prompt version, and config hash.

#### BENCH-013 — Timeouts and censoring
U · **Must** · Depends: BENCH-004
A run not diagnosed or recovered within 20 minutes records `timed_out=true`; the report treats it as censored at 20 minutes and lists it.
**Accept:** Report section "Timeouts" exists, even if empty.

#### BENCH-014 — Publication
U+T · **Must** · Depends: BENCH-011
Publish the raw CSV, `scenarios.yaml`, chaos scripts, `PROCEDURE.md`, the report, the charts, and the list of failures in the repository and link them from the README.
**Accept:** README "Results" section links all artifacts.

---

## 12. Traceability

| Spec feature | Requirements |
|---|---|
| Incident detection | FR-001, FR-002, FR-003, FR-006, NFR-002 |
| Alert ingestion | FR-004, FR-005, FR-007, FR-008, API-003 |
| Log investigation | AI-005, AI-011, SEC-008 |
| Metrics investigation | AI-006 |
| Recent deployment analysis | AI-007, TB-009 |
| Service health checks | AI-008, SEC-014 |
| Runbook retrieval | AI-009, AI-026, AI-027, DB-009, TB-013 |
| AI hypothesis generation | AI-012, AI-013, AI-020 |
| Evidence linking | AI-010, AI-014, MOB-004, MOB-005, BENCH-009 |
| Reflection / verification | AI-014, AI-015, AI-016 |
| Confidence scoring | AI-016, AI-017 |
| Action proposal | AI-018, AI-019, RUN-007 |
| Human approval | API-008, API-009, API-010, SEC-001, MOB-008 |
| Biometric approval | MOB-009, SEC-012 |
| Dry runs | RUN-007, MOB-007 |
| Safe execution | RUN-001…RUN-018, SEC-002, SEC-003, FR-024 |
| Rollback | FR-015, FR-016, RUN-010, RUN-011, MOB-011, TEST-007 |
| Audit logging | DB-008, SEC-017, NFR-012, API-012, MOB-012 |
| Push notifications | FR-018, MOB-016 |
| WebSockets | API-014…API-017, MOB-014 |
| Incident history | FR-020, API-004, MOB-003 |
| Stale-state handling | MOB-015, API-008, FR-017, TEST-006 |
| Prompt-injection defence | SEC-005, SEC-006, SEC-007, AI-011, AI-028, TEST-003 |
| Secret redaction | SEC-008, SEC-009, AI-008, TEST-008 |
| Escalation | FR-010, FR-024, AI-017, AI-018, AI-025 |
| Agent budgets | AI-002, AI-003, AI-029 |
| Documented limitations | SEC-018 |
| Benchmark | BENCH-001…BENCH-014 |
