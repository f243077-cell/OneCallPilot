# B3.4 / NFR-001 — alert to proposal

> Target (NFR-001): the **median** from `incident.opened_at` to `proposal.created_at` is at most 60 s. Measured on Usman's laptop against Supabase `ocp-dev`, `google/gemini-3.8-flash` through OpenRouter, prompts v2. Method: the incident's audit trail (`GET /audit?incident_id=…`): every `llm.call` row carries its own latency; the time between rows is everything else.

## Status: **not met yet** (2026-11-08): about 72 s, of which about 40 s is waiting for the database

## Where the time goes: `ocp smoke` run 12 (scenario 2, 2026-11-07)

| + s after `incident.opened` | Step | Model time |
|---|---|---|
| 0.0 | incident opened by the detector | |
| 0–8 | job pickup, context load (database) | |
| 8–12 | gather turn 1 → `get_recent_deploys` | 4.0 s |
| 12.6 | evidence E2 stored | |
| 16.5–18.5 | gather turn 2 → `query_logs` | 2.0 s |
| 23.4–25.7 | gather turn 3 → `search_runbooks` | 2.3 s |
| 35.7–37.9 | gather turn 4 → `get_service_health` | 2.2 s |
| 41.2–45.3 | gather turn 5 (done, with text) | 4.1 s |
| 47.0–49.7 | hypothesize | 2.7 s |
| 54.9–58.2 | disproof plan (then the disproof query) | 3.3 s |
| 58.8–61.7 | reflect | 2.9 s |
| 66.8–70.2 | propose | 3.4 s |
| 72.0 | proposal created (after the dry run) | |
| | **total model time** | **27.0 s of 72.0 s** |

Between almost every two steps there are **about 4 seconds without model work**.

## Cause: every database round trip takes about 680 ms

From the worker container to `ocp-dev` (2026-11-08): connect 2.8 s, then `select 1` eight times: **672–681 ms each**. The project is in **`ap-southeast-2` (Sydney)**; the laptops are in Pakistan.

Each step writes to the database before the next one starts:
- storing one evidence item is one transaction of about 7 round trips (`BEGIN`, lock the incident, next ref, insert, bump the state version, audit row, `COMMIT`): about 4.8 s;
- every LLM call writes its `llm.call` audit row (about 1.4 s with the pool acquire) before the next call.

So roughly **40 of the 72 s are database latency**, not the model and not the tools.

## Options

| Option | Effect | Cost and risk |
|---|---|---|
| **A. Create `ocp-bench` in `ap-south-1` (Mumbai)** before the freeze. `ocp-bench` is still empty (ADR-28); a Supabase project's region cannot be changed after creation. | Every round trip from Pakistan should fall to tens of milliseconds: most of the 40 s go. The counted runs are measured against `ocp-bench`. | Tanzeel creates the project and migrates it (`CLAUDE.md` §4.7); new keys in `.env` and the APK. No code change. **Decision for Tanzeel.** |
| B. Fewer round trips per evidence write (one statement instead of seven). | About 4 s less per tool step on any region. | Risky: the detector's deduplicated alerts insert evidence concurrently, and a single statement's snapshot can pick a duplicate ref. Needs a careful design and tests. |
| C. Write the `llm.call` audit rows in the background, waiting for them before the run ends. | About 1.4 s less per LLM call (about 13 s per investigation). | Changes when audit rows are written (still all of them). Audit is a safety property: needs agreement first. |
| D. Parallel tool calls in one gather turn; fewer gather turns. | Up to a few seconds. | Depends on the model; budgets unchanged. |

**Recommendation:** A first (no code, biggest effect), then measure again. B and C only if the median is still above 60 s afterwards.

## Gate G2 run (2026-11-08, back to back, ADR-27)

Alert to proposal = proposal time minus incident time, as the driver saw them (3 s polling):

| Scenario | Incident (s after injection) | Proposal (s after injection) | Alert to proposal |
|---|---|---|---|
| 2 bad deploy | 9.0 | 85.1 | 76 s |
| 3 db pool | 17.7 | 96.7 | 79 s |
| 4 cache outage | 12.1 | 92.8 | 81 s |
| 5 traffic spike | 50.5 | 140.3 | 90 s |
| 6 config crash | 0.7 | 86.2 | 86 s |
| **median** | | | **81 s** (target 60 s) |

Scenario 8 escalated `budget_exhausted`: its investigation passed `MAX_AGENT_SECONDS` (90 s). With about 40 s of database waiting per run, the 90 s budget leaves about 50 s for the model and the tools, so the database latency can change outcomes, not only times.

## Next
- Decision on option A (Tanzeel). After it, measure 5 runs on the new region.
