# Gate G3 — "approve on a real phone, end to end" (`docs/phases.md`, Phase 3)

> Tanzeel's phone (Sparx Note 20, Android, live debug build) on the same Wi‑Fi as the laptop; the full stack on the laptop (`ocp up`).

## Status: **open** (2026-11-04)

| # | Criterion | State | Evidence |
|---|---|---|---|
| 1 | With the app killed, scenario 2 produces a push; the tap opens the incident; biometric approval leads to the rollback executing and the incident resolving; recorded as a draft demo clip | **Push half passed** 2026-11-04; the approval half needs a proposal (OpenRouter credit, A3.3) | `push.md` ("App killed") |
| 2 | Wi‑Fi off on the phone during review shows the stale banner and disables Approve; reconnecting re-syncs | **Banner and re-sync passed** 2026-11-04 on the device; Approve disabled is covered by widget tests (A3.4) and checked on the device with A3.3 | below |
| 3 | A double tap on Approve produces one execution | Integration and widget tests pass; on the device with A3.3 | — |
| 4 | All screens are wired live; widget tests are green | A3.1, A3.6 done | `mobile` CI |
| 5 | The bench procedure dry run completed without manual database edits | B3.9 (Usman, OpenRouter) | — |

## Criterion 2 on the device (2026-11-04, Tanzeel)
1. The app open on the scenario 2 incident (escalated `llm_unavailable`), connected (no banner).
2. Wi‑Fi off, mobile data off: the banner "Reconnecting — approval is paused" appeared within seconds.
3. Wi‑Fi on: the banner disappeared and the screen refreshed (resync after `hello`, A3.1 / MOB-015).

No proposal existed (no OpenRouter credit on this laptop), so there was no Approve button to see disabled.
