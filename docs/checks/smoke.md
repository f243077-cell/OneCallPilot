# S2.1 / TEST-014 — `ocp smoke` live runs

> `uv run --project tools/ocp ocp smoke` on Tanzeel's laptop: the full stack (`ocp up`, backend included) against Supabase `ocp-dev`, the agent on OpenRouter (`google/gemini-3.8-flash`, ADR-23). Scenario 2 (bad-deploy): reset → 240 s warm-up → inject → incident → proposal → challenge and approve as the test user → resolved within 5 minutes of the approval.

## Status: **not passed yet** (2026-11-03): blocked on OpenRouter credit

Each run got further, and each stop found a real problem, fixed and pushed:

| # | Date (UTC) | Stopped at | Cause | Fix |
|---|---|---|---|---|
| 1 | 2026-11-02 22:29 | no incident | The Docker VM clock had jumped 13 h (Windows time zone): Prometheus rejected samples, cs-lb's nginx kept a stale api address | Time zone set to Pakistan Standard Time; observability volumes recreated; stack restarted (`docs/checks` memory: restart after a clock jump) |
| 2 | 2026-11-02 22:59 | incident at +19 s, then `GET /incidents/{id}` → 500 | C1 `AgentMeta.provider` did not allow `openrouter` | `603bd7c` (contracts 0.2.0) |
| 3 | 2026-11-02 23:19 | no incident | The detector learnt the previous run's fault during the reset's warm-up (`IndexError` as a known type, 11 % errors as a fresh baseline) | `908f495`, ADR-24: nothing learnt for the first 150 s of a warm-up |
| 4 | 2026-11-02 23:36 | no incident | as 3 (before the fix) | — |
| 5 | 2026-11-02 23:46 | incident at +10.5 s; six gather calls; escalated `llm_unavailable` | not visible: the adapter dropped the reason | `597c887`: the reason is logged (status, code, clipped message) |
| 6 | 2026-11-03 06:12 | incident at +30.8 s; escalated `llm_unavailable` | **HTTP 402**: "This request requires more credits, or fewer max_tokens … can only afford 7396" | needs OpenRouter credit (Tanzeel) |

What already works end to end on the real stack: detection within 11–31 s of the injection, the agent's gather phase with real tools (deploys, logs, metrics, health, runbook search over OpenRouter embeddings), evidence stored and published, escalation with the evidence kept.

## Next run
After credit is added: `uv run --project tools/ocp ocp smoke`; record the timings it prints (`+N s` for incident, proposal, approval, resolution) in this file.
