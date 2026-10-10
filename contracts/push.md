# C9 — Push payload

> **Contract:** C9 · **Drafter:** Usman · **Reader:** Tanzeel · **Version:** tracked by `contracts/VERSION`
> **Source:** `docs/architecture.md` §2.12, §5.8, §9.7, §10.5 · **Requirements:** FR-018, MOB-016, SEC-009
> **Model:** `python/oncallpilot_contracts/push.py` → `schemas/push_payload.json`
>
> Adding a key or a type is a contract change (`CLAUDE.md` §8.4).

---

## 1. Message

Every push is one FCM HTTP v1 message, sent by backend-api or backend-worker through `firebase-admin`, with a **data** part and a **notification** part.

**Data** — exactly these five keys, all strings, and nothing else:

| Key | Value |
|---|---|
| `type` | One of the five types in §2 |
| `incident_id` | Incident UUID |
| `severity` | `sev1`, `sev2`, or `sev3` |
| `service` | `api`, `worker`, `lb`, `payments`, `redis`, or `postgres` |
| `title` | The incident title, at most 120 characters, e.g. `api error rate 31%` |

```json
{"type": "proposal.created", "incident_id": "6f1c2a4e-0b7d-4c1e-9a53-2d8e4f6a7b01",
 "severity": "sev1", "service": "api", "title": "api error rate 31%"}
```

**Notification** — `title` is the data `title`; `body` is the text for the type in §2. The payload **never** contains evidence, log text, hypotheses, tokens, or any secret (SEC-009). The app fetches everything else over REST after the tap.

## 2. Types

| `type` | Sent when | Android priority | Notification body |
|---|---|---|---|
| `incident.opened` | An incident is opened (`incident.opened`) | `high` | "New incident — investigating" |
| `proposal.created` | A proposal needs a decision, including rollback proposals | `high` | "Action needs your approval" |
| `incident.escalated` | The incident is escalated, for any reason in architecture §5.7 | `high` | "Needs human investigation" |
| `incident.action_failed` | An executed action failed its health check | `high` | "Action failed — review the incident" |
| `incident.resolved` | The incident is resolved | `normal` | "Resolved" |

A push goes to every device with `push_enabled = true` whose incident severity is at or above `monitor_settings.notify.min_push_severity`. A token that FCM reports as unregistered is deleted (FR-018).

## 3. Android

- **Channel:** `incidents_critical`, created by the app with high importance (MOB-016). Android 13+ asks for `POST_NOTIFICATIONS` first.
- **Tap:** opens `/incidents/{incident_id}` through go_router, with the app in the foreground, in the background, or killed.
- **App in the foreground:** the WebSocket already delivered the event; the app shows an in-app banner and does not rely on FCM (architecture §5.8).
