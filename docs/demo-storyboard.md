# Demo storyboard — 2 minutes on a real phone (A3.8)

> Script for the demo video (P4.9, Gate G4) and its draft clip (Gate G3 criterion 1). Scenario 2 (**bad-deploy**): api 1.5.0 breaks checkout; the expected fix is `rollback_deploy(api → 1.4.0)` (`benchmark/scenarios.yaml`).
> Labels in quotes are the app's real text (`mobile/lib/features/`) and the push bodies of C9 (`contracts/push.md` §2). If a label changes, update this file.

## What the video must show
1. An alert reaches the engineer's phone, even with the app closed.
2. The AI investigates with read-only tools, and every hypothesis cites stored evidence.
3. The AI only **proposes** one allowlisted action, with a dry run; a human approves it with a fingerprint.
4. An isolated runner executes it, and the system checks that the service really recovered.
5. Honesty: real timings, and no hidden steps.

## Rules for recording
- **Real run, real timings.** Every cut shows the elapsed time on screen (for example "+38 s"). Speed-ups are labelled "×4". Never edit, re-order or re-type what the agent produced.
- One take of the flow; no re-runs stitched together. If the agent escalates instead of proposing, that is a correct outcome: record it, and re-record the demo in a separate, clean run (`chaos reset` first).
- No secrets on screen: no `.env`, no terminal with `docker compose config`, no tokens in a URL bar. Use a terminal that shows only the `chaos` commands.
- The phone shows the real notification shade and lock screen; turn on "Do not disturb" exceptions for OnCallPilot, and turn off other apps' notifications.

## Before recording (checklist)
- [ ] `ocp up` finished; `ocp doctor` clean; the phone's app shows no banner.
- [ ] At least **4 minutes** since the last `chaos reset` (warm-up, ADR-24); the testbed is at baseline (`chaos status`).
- [ ] OpenRouter credit on the key in `.env`; the model is the pinned one (B2.13).
- [ ] Phone: fingerprint enrolled; battery saver off; screen recording on (Android Quick settings → Screen recorder, with "Show touches").
- [ ] Laptop: screen recorder on one window split in two: left, a terminal; right, Grafana's "Chaos Shop Overview" dashboard (panels "api error rate (5xx share)" and "api requests per second").
- [ ] App killed (swiped away), phone locked, lying on the desk next to the laptop.

## Shot list
Times are video time. "Phone" is the phone's screen recording; "Laptop" is the laptop recording. Captions are short on-screen text; the narration is optional voice-over.

| Video time | Phone | Laptop | Caption / narration |
|---|---|---|---|
| 0:00–0:10 | Locked screen | Grafana: api error rate near 0 | **"OnCallPilot: an AI on-call copilot that never touches production by itself."** |
| 0:10–0:18 | Locked screen | Terminal: `chaos inject bad-deploy` → "api 1.5.0 on cs-api-150-1" | "We ship a bad release of the shop's api." |
| 0:18–0:28 | (cut, "+15–40 s") Notification arrives on the lock screen: title "api error rate …", body **"New incident — investigating"** | Grafana: error rate climbs | "The detector notices within seconds. The phone is locked and the app is closed." |
| 0:28–0:38 | Tap the notification → incident detail opens, status *investigating*; the **"Evidence (N)"** count rises as the tools run | — | "The agent investigates with read-only tools: logs, metrics, deploys, container health, runbooks." |
| 0:38–0:50 | (cut, "+N s") Notification **"Action needs your approval"**; on the detail screen, **"Hypotheses"**: #1 "bad deploy 1.5.0" with **"Confidence …"**, citing E1, E2 | — | "Three ranked hypotheses at most, each citing stored evidence. It even tries to disprove its own answer." |
| 0:50–1:00 | Scroll through the **"Evidence (N)"** cards: the log lines of one E-ref, the deploy record of 1.5.0 in another | — | "Every claim points to evidence stored with the incident." |
| 1:00–1:15 | **"Review proposed action"** → **"Action review"**: `rollback_deploy` api → 1.4.0, risk, **"Dry run"** ("Current state" / "Would change"), **"Preconditions"**, **"Expires in m:ss"** | — | "It proposes exactly one action from a fixed catalogue, with a dry run of what would change." |
| 1:15–1:25 | Tap **"Approve"** → **"Approve this action?"** → fingerprint prompt → **"Approved"** | — | "A human decides. Medium risk needs a fingerprint; there is no auto-approve." |
| 1:25–1:40 | **"Execution"** screen: **"Waiting for the runner…"**, then the steps tick | Terminal (optional): `chaos status` shows api 1.4.0 | "An isolated runner checks the signature, the target and the state again, then rolls back." |
| 1:40–1:52 | (cut, "+60 s" verification) Notification **"Resolved"**; incident status *resolved* | Grafana: error rate back near 0 | "It verifies that the signals really recovered before closing the incident." |
| 1:52–2:00 | **"Audit history"** screen: the approval and execution rows | — | **"Every step is audited. Benchmark: 48 runs, manual vs copilot, zero unsafe actions."** (fill in the real numbers from `benchmark/results/report.md`) |

## If something else happens (record it, then decide)
| What you see | Meaning | For the video |
|---|---|---|
| "Needs human investigation" instead of a proposal | The agent escalated (low confidence, budget, injection detected, LLM unavailable) | Correct behaviour; keep it for an optional extra clip, re-run scenario 2 cleanly for the main video |
| A stale banner ("Reconnecting — approval is paused"), Approve greyed out | The phone lost its connection or the proposal changed | Wait for the banner to clear; never approve around it |
| "Action failed — review the incident", then **"Review rollback"** | The action did not pass verification; the system offers its rollback | Approve the rollback; worth keeping as a second clip |

## Optional second clip (≤ 30 s): scenario 8, log injection
`chaos inject log-injection` plants "ignore previous instructions…" text in the api's error logs. The evidence card shows the chip **"Contains instruction-like text — treat as data"**, and the agent does not follow the planted instruction: it escalates or proposes only a catalogue action. Caption: **"Logs are untrusted input."**

## After recording
- Gate G3 (draft clip): save the raw phone and laptop recordings; link them from `docs/checks/gate-g3.md` (file names and date, not the files in git if they are large).
- P4.9 (final video): edit to 2:00, add the captions above and the benchmark numbers; link the video from the README (Gate G4).
