# P4.3 — clean-machine setup test

> Goal (`docs/phases.md` Phase 4): a stranger can run the project from a fresh clone. Steps: `git clone`, copy the git-ignored files (`.env`, `.env.local`), `ocp up`, `ocp doctor`, `ocp smoke`, with **no manual fix**. It counts on the commit that is frozen, so it is repeated at P4.4.

## Status: **not passed yet** (2026-11-09); repeat on the freeze commit

## Attempt 1 — 2026-11-07, Usman's laptop, first ever setup (branch history)
The first `ocp up` on a fresh host found two bugs, both fixed: the backend image was never built (`af7be4c`), and Grafana's first start ran past its healthcheck (`26849ee`). After them, `ocp up` brought 17 services to healthy and `ocp smoke` passed (`docs/checks/smoke.md`, run 12).

## Attempt 2 — 2026-11-09, fresh clone of `main` at `8b0fbbe`
`git clone` into a new folder, the two env files copied in (`git check-ignore` lists both), then `ocp up`, `ocp doctor`, and two `ocp smoke` back to back. Docker's image cache was warm (images built earlier on this laptop), so the time below is not a first build.

| Step | Result |
|---|---|
| `ocp up` | **failed** after 258 s: `oncallpilot-alloy-1 is unhealthy`. Alloy became healthy on its own a little later. The same happened once on 2026-11-08. Cause: its healthcheck allows only 15 s plus 6 tries (about 75 s) and the laptop was busy (Chrome and VS Code about 30 % CPU, total 73–100 %). **Fixed:** `start_period` 120 s (`infrastructure/compose/observability.yml`), as for Grafana. |
| `ocp doctor` | one failure after the smokes: `cs-api-140-1 is exited`, which is the bad-deploy state the second smoke left behind, not a setup fault |
| `ocp smoke` 1 | stopped at `chaos reset` (the testbed was still settling after the failed `ocp up`) |
| `ocp smoke` 2 | incident at +24.4 s, then **escalated `budget_exhausted`**: the investigation passed its 90 s budget. About 40 s of every investigation is database latency to the Sydney region (`docs/checks/latency.md`), and the laptop was busy; together they ran out the budget. |

The clone, with its copies of the env files, was deleted afterwards.

## What it taught (now in the README's Setup section)
- Windows needs WSL 2 and a Docker disk image on a drive with room; a full C: makes Docker's disk read-only.
- `.env.local` (anon key and the test user) is needed by `ocp smoke` and `bench`.
- The Supabase project needs an asymmetric JWT signing key, sign-ups off and a test user.
- Live runs need an otherwise idle laptop.

## Next
1. Repeat on the freeze commit with an idle laptop: `ocp up` (with the Alloy fix), `ocp doctor`, `ocp smoke` twice back to back (also U4, ADR-27's live proof).
2. The 90 s agent budget is tight while the database is in Sydney: `ocp-bench` in `ap-south-1` (`docs/checks/latency.md`, option A) would remove most of that risk for the counted runs.
