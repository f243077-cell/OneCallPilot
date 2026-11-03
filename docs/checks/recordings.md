# B2.6 / S2.3 — tool outputs recorded from the live testbed (TEST-002)

> `uv run python ../tests/agent_regression/record.py all` from `backend/`, on Tanzeel's laptop with the full stack up (`ocp up`, Supabase `ocp-dev`). Per scenario: `chaos reset` → 240 s warm-up → `chaos inject` → wait for the incident the real detector opens → record what the scenario's scripted tool calls read. Output: `tests/agent_regression/scenarios/recorded/s<N>_*.yaml` (commit `6eef01a`).

## Status: **done** (2026-11-03)

All 8 recorded; the agent regression suite runs the 8 synthetic and the 8 recorded files (16 cases, green locally and in CI's `backend` job). No value of `.env` / `.env.local` and no token pattern appears in any file (the recorder refuses to write otherwise; checked again by hand).

| # | Scenario | Incident (service, title) | Injection → incident |
|---|---|---|---|
| 1 | memory-leak | worker, "worker 2 restarts" | 7 min 19 s (the leak takes minutes to crash the worker; includes a 2-minute internet drop) |
| 2 | bad-deploy | api, "api error rate 4%" | 53 s |
| 3 | db-pool | api, "api p95 latency 8500 ms" | 49 s |
| 4 | cache-outage | redis, "redis 0 restarts" (no running container) | 14 s |
| 5 | traffic-spike | api, "api p95 latency 768 ms" | 40 s |
| 6 | config-crash | worker, "worker 2 restarts" | 35 s |
| 7 | slow-dependency | api, "api p95 latency 3610 ms" | 49 s |
| 8 | log-injection | api, "api 8 stack traces/min" | 15 s |

Times are from the recorder's "inject" line (after `chaos inject` returned) to its first poll that saw the incident (every 5 s).

## Found while recording, and fixed
| Problem | Fix |
|---|---|
| Scenario 5 right after scenario 3 (both api `p95_latency`) opened no incident: the signal stayed triggered across the reset (recovery needed 150 s settle + 180 s; the run injects at 240 s) | ADR-25, `b547633` |
| After scenario 5, `chaos reset` failed five times in a row: the spike traffic kept the restarted api slot from passing its health check within 60 s, and the reset turned the spike off only after that wait | `5dc01bb` (TB-007) |
| The API answered 500 while the laptop's internet (and Supabase) was briefly gone; the Supabase token expires after 1 h | the recorder retries for 2 minutes and signs in again |

## Open
- **Scenario 8:** the planted text is in an INFO line's `msg` and in the ERROR line's `exc_message`. `query_logs` returns only `msg` and `exc_type`, so a query for ERROR logs never sees it; the recorded file expects no flagged evidence (`EXPECT_OVERRIDES` in `record.py`). The synthetic s8 and `tests/security` still cover TEST-003. Whether the testbed should put the text where an ERROR query sees it is Tanzeel's call.
- One reset failed once with `cs-worker-210` not healthy after 60 s (the retry passed). The health wait is 60 s; TB-007 allows 90 s for the whole reset.
- The recorded-model replay (`@pytest.mark.recorded`) needs OpenRouter credit.
