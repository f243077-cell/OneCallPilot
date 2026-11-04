# A3.2 / MOB-016 / FR-018 — push on the real phone

> Tanzeel's phone (Sparx Note 20, Android) with the live debug build (`165acd4` + `c0d7f48`), on the same Wi‑Fi as the laptop; the full stack with the Firebase service account mounted (`df14a82`, `ba5a73c`).

## Status: **passed** (2026-11-03, Tanzeel)

| Step | Time (UTC) | Result |
|---|---|---|
| Sign-in on the phone | 19:35 | `POST /devices` registered the phone (Android, app 1.0.0+1, push on) |
| `chaos inject bad-deploy` | 20:05:21 | api 1.5.0 serving |
| Incident opened | 20:05:45 (+24 s) | `stack_traces` on api; push "New incident — investigating" received |
| Escalated | 20:05:57 | `llm_unavailable` (no OpenRouter credit); push "Needs human investigation" received |
| Tap on the notification | | opened the incident's detail screen |

- The registration of an earlier install (15:53) was reported unregistered by FCM on the first send and disabled by the backend, as FR-018 requires.
- No `push refused by FCM` or `push not sent` warning in either backend service.

## Found on the device, and fixed
| Problem | Fix |
|---|---|
| The app showed "Offline": the laptop's Wi‑Fi address had changed (DHCP), and the build's `API_BASE_URL` pointed at the old one | Rebuilt for the new address; a DHCP reservation on the router keeps it fixed |
| The app showed "Reconnecting — approval is paused" with a healthy socket: after `hello`, the refetch read the feed through the socket provider's own ref, a circular dependency that debug builds throw as an `Error` the resync did not catch | `c0d7f48` (refetch through `ref.container`; `test/main_test.dart`) |

## App killed (MOB-016 acceptance, push half of Gate G3 criterion 1): **passed** (2026-11-04, Tanzeel)
The app was swiped away from recent apps and the screen locked before the injection.

| Step | Time (UTC) | Result |
|---|---|---|
| `chaos reset` (by `ocp up`) | 17:29:32 | warm-up starts |
| `chaos inject bad-deploy` | 17:33:46 → 17:34:02 | api 1.5.0 on `cs-api-150-1` |
| Incident opened | 17:34:17 (+15 s) | `stack_traces` on api; push received |
| Escalated | 17:34:26 | `llm_unavailable` (no OpenRouter credit on this laptop); push received |
| Tap on the notification | | the app started and opened the incident's detail screen |

An unrelated incident (worker `restarts`, 17:32:37, before the injection) also pushed and escalated; see the 2026-11-04 `handoff.md` entry.

## Not yet checked
- A push for a proposal (`proposal.created`) needs a proposal, so OpenRouter credit.
