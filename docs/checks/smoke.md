# S2.1 / TEST-014 — `ocp smoke` live runs

> `uv run --project tools/ocp ocp smoke` on Tanzeel's laptop: the full stack (`ocp up`, backend included) against Supabase `ocp-dev`, the agent on OpenRouter (`google/gemini-3.8-flash`, ADR-23). Scenario 2 (bad-deploy): reset → 240 s warm-up → inject → incident → proposal → challenge and approve as the test user → resolved within 5 minutes of the approval.

## Status: **PASSED** (2026-11-07, run 12, Usman's laptop, prompts v2)

Earlier status (2026-11-03): not passed, blocked on OpenRouter credit.

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

### 2026-11-07, Usman's laptop (fresh host, his own OpenRouter key)

`ocp up` itself first failed twice on the fresh host: the backend image was never built (fixed in `af7be4c`), and Grafana's first start ran past its healthcheck (`26849ee`). Then:

| # | Time (UTC) | Prompts | Stopped at | Cause | Fix |
|---|---|---|---|---|---|
| 7 | 10:54 | v1 | `chaos reset`: cs-api not healthy after 60 s | A game (FC 25) was using the CPU: cs-api's 3 s healthchecks timed out while it served traffic normally | none needed; machine load |
| 8 | 11:05 | v1 | incident at +12.2 s; escalated `low_confidence`, no hypothesis stored | The detector's first alert timed out client-side but opened the incident; its retry added a second detector signal (E3) while the agent gathered. The model cited E3 without having been shown it, and the correct `bad_deploy` hypothesis (0.95) was dropped as citing an unknown reference (AI-013) | `a7307c5`: evidence added during the run is loaded and shown before the hypothesis step |
| 9 | 11:27 | v1 | "no incident for api within 300 s" | A `p95_latency` alert opened an api incident 10 s before the injection (end of the warm-up); the fault's alerts were deduplicated into it | `79b9eb8`: the smoke stops before injecting and names that incident |
| 10 | 11:38 | v1 | incident at +24.3 s; escalated `low_confidence` | Right hypothesis (`bad_deploy` 0.95, reflection 0.98) but it cited only detector signals, not the deploy ledger; the citation check halved it to 0.47 (AI-014). The model is not told the rules | `a949f46`: prompts v2 state the citation rules |
| 11 | 12:08 | v2 (first draft) | incident at +26.1 s; escalated `low_confidence` | v2 worked (the deploy ledger was cited), but the disproof query found the same error 5 minutes before this deploy: left by run 10's escalated fault, inside the tools' 30-minute look-back (`tools/window.py`). The model read it as a contradiction | A fair run needs 30 healthy minutes before the injection. The v2 text was also corrected after review before any push (see `a949f46`) |
| 12 | 19:50 | v2 | **PASS** | — | — |

OpenRouter spend for runs 8–11: about $0.11 (2–3 cents per run).

**Run 12 (PASS)**, `ocp smoke` on a stack that had been down for hours (no earlier fault in the 30-minute look-back), `PROMPT_VERSION=v2`, `google/gemini-3.8-flash`:

| Step | Seconds after the injection |
|---|---|
| incident (api, 5 stack traces/min) | +17.6 |
| proposal `rollback_deploy {"service": "api", "target_release": "1.4.0"}`, risk medium, needs biometric | +95.0 |
| challenge and approve as the test user (`auth_method: biometric`) | +104.9 |
| resolved (`action_succeeded`) | +240.8 (136 s after the approval; TEST-014 limit 300 s) |

Alert to proposal took about 77 s: over the 60 s of NFR-001, so B3.4 has real work.

### Lessons for live runs
- Keep the laptop otherwise idle (run 7).
- Leave 30 healthy minutes between a fault and the next injection: the tools look back 30 minutes (`tools/window.py`), so an earlier fault's errors read as a contradiction (run 11). `bench run` waits only 4 minutes between runs; raised for a decision before the pilot (`handoff.md`, 2026-11-08).
- If the warm-up opens an incident, `ocp smoke` now stops and says so (run 9); just run it again.

## Next run
Re-run after changes to the agent, the prompts or the detector: `uv run --project tools/ocp ocp smoke` with 30 healthy minutes before it; record the timings here.
