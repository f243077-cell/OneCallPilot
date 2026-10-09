# C3 — WebSocket protocol

> **Contract:** C3 · **Drafter:** Usman · **Reader:** Tanzeel · **Version:** tracked by `contracts/VERSION`
> **Source:** `docs/architecture.md` §5.8, §9.4, §10.3, §10.4 · **Requirements:** API-014, API-015, API-016, API-017, FR-019, SEC-011, MOB-014, MOB-015, NFR-003, NFR-014
> **Models:** `python/oncallpilot_contracts/ws.py` → `schemas/ws_client_message.json`, `schemas/ws_server_message.json`. **Fixtures:** `fixtures/ws/` (one file per message; see §8).
>
> Changing a message, a field, a close code, or a timing value is a contract change (`CLAUDE.md` §8.4).

---

## 1. Connection

- URL: `ws://<host-LAN-IP>:8000/ws/incidents` on backend-api. The URL never carries a token or any other query string (SEC-011).
- Every frame is a **text** frame holding one JSON object (UTF-8). Every object has a `type`.
- Unknown fields are not sent. A client maps an unknown `type`, `event`, or enum value to an `unknown` case and ignores it (MOB-020).

## 2. Messages

**Client → server**

| `type` | Fields | When |
|---|---|---|
| `auth` | `token`: the Supabase access token | First message, within **5 s** of connecting. Sent again on the open socket whenever the token is refreshed. |
| `resume` | `last_event_id` | Optional, right after `hello`: replay what was missed. |
| `pong` | — | The answer to every `ping`. |

**Server → client**

| `type` | Fields | When |
|---|---|---|
| `hello` | `server_time`, `contracts_version`, `latest_event_id` (null while the stream is empty) | Once, after the first `auth` succeeds |
| `ping` | — | Every **15 s** |
| `resync_required` | — | The `last_event_id` of a `resume` is older than the retained stream |
| `event` | the envelope in §4 | Every entry of `ocp:events`, in order |

## 3. Lifecycle

1. **Connect, then authenticate.** The client sends `auth` within 5 s, or the server closes with **4408**. An invalid token closes with **4401** (API-014).
2. **Hello.** After the first successful `auth`, the server sends `hello`. Nothing is sent before authentication. The client keeps `server_time − local time` as its clock offset for every countdown (NFR-014).
3. **Catch up.**
   - *First connect* (no stored event ID): fetch `GET /incidents` (and the open details), then apply live events. Events already reflected in the snapshot are dropped by the `state_version` rule in §5.
   - *Reconnect:* send `resume {last_event_id}`. The server replays every later event in order, then continues live, with no gap and no duplicate (API-016). If the ID is too old, the server sends `resync_required`; the client then refetches as on a first connect.
4. **Live.** The server forwards every new `ocp:events` entry. On the LAN an event reaches the app within 2 s (p95) of its commit (NFR-003).
5. **Token refresh.** The client sends a new `auth` on the open socket. If the token expires without one, the server closes with **4401**.
6. **Heartbeat.** The server sends `ping` every 15 s and closes a connection that misses **2** consecutive `pong` replies, so a dead client is closed within 45 s (API-017).

## 4. Event envelope

```json
{"type": "event", "event": "proposal.created", "event_id": "1791968465000-0",
 "incident_id": "6f1c2a4e-0b7d-4c1e-9a53-2d8e4f6a7b01", "state_version": 8,
 "ts": "2026-10-14T09:01:05Z", "data": {"…": "…"}}
```

| `event` | `data` | Schema (inside `ws_server_message.json`) |
|---|---|---|
| `incident.opened` | `IncidentSummary`; its `id` and `state_version` equal the envelope's | `IncidentSummary` |
| `incident.updated` | `{status, status_reason, severity}` | `IncidentUpdatedData` |
| `evidence.added` | an `Evidence` item plus `payload_truncated`; the payload is cut to 4 KB when needed | `EvidenceAddedData` |
| `hypothesis.updated` | `{hypotheses: Hypothesis[]}`: the full current set | `HypothesisUpdatedData` |
| `proposal.created` | `Proposal` (also for rollback proposals) | `Proposal` |
| `proposal.updated` | `{proposal_id, status, status_reason, superseded_by}` | `ProposalUpdatedData` |
| `execution.progress` | `{execution_id, proposal_id, step, status, message}` | `ExecutionProgressData` |
| `incident.resolved` | `{resolution, resolved_at}` | `IncidentResolvedData` |

The shapes of `IncidentSummary`, `Evidence`, `Hypothesis`, and `Proposal` are C1 (`schemas/incident_summary.json` and so on). An evidence payload has one shape per `kind` (`schemas/evidence_payload_<kind>.json`).

## 5. Ordering and duplicates

- `event_id` is the Redis stream ID of the entry in `ocp:events`: monotonic, and unique even within one millisecond (`<ms>-<seq>`).
- Every persisted change to an incident increments its `state_version` and publishes exactly one event carrying the new value (FR-009, FR-019).
- The client **drops an event whose `state_version` is lower than the one it holds** for that incident. This removes duplicates after a resume or a REST refetch.

## 6. Client behaviour (MOB-014, MOB-015)

- **Reconnect** with backoff 1, 2, 4, 8, 16, then 30 s at most, with ±20 % jitter. On every connect: `auth`, then `resume` if the client has a `last_event_id`.
- **Stale** when no message (including `ping`) has arrived for 30 s, or the socket has been closed for more than 3 s. While stale, the app shows `StaleBanner`, disables **Approve**, and keeps **Reject** enabled (C8 §4).
- Approve is enabled only when the connection is `connectedSynced`, which means `hello` was received and any `resume` or refetch has completed.

## 7. Close codes

| Code | Meaning | Client |
|---|---|---|
| `4401` | Invalid token, or the token expired without a refresh | Refresh the session, then reconnect; sign out if the refresh fails |
| `4408` | No `auth` within 5 s | Reconnect |
| any other | Network loss, server restart, missed pongs | Reconnect with backoff |

## 8. Fixtures (`fixtures/ws/`)

Client messages: `auth.json`, `resume.json`, `pong.json`. Server messages: `hello.json`, `ping.json`, `resync_required.json`, and one per event: `incident.opened.json`, `incident.updated.json`, `evidence.added.json`, `hypothesis.updated.json`, `proposal.created.json`, `proposal.updated.json`, `execution.progress.json`, `incident.resolved.json`. They describe the scenario-2 incident used across the contract fixtures. Full incident sequences are in `fixtures/timelines/`.
