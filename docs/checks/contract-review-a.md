# Contract review (Stream A) — Usman's C1, C2, C3, C5, C8, C9

> **Reviewer:** Tanzeel (Stream A) · **Date:** 2026-10-09 · **Reviewed:** `origin/usman` at `0582f37`, `contracts/` only, read-only (detached worktree outside the repo) · **Against:** `docs/architecture.md`, `docs/requirements.md`, `CLAUDE.md` §8, and C4/C6/C7 on `tanzeel`.
> Nothing in `contracts/` was changed by this review. Paths below are on `origin/usman`.

## What was checked and is fine

- His suite: `uv sync --locked`, **246 tests pass**, `ruff` and `mypy` clean, `schema_export` regenerates every schema with no diff.
- `openapi.yaml` refers to the generated `schemas/*.json` (78 `$ref`s), so C2 and the Python models cannot drift apart; `test_openapi.py` checks the §9.2 operation list.
- Enums match architecture §5.7, §6.3, §6.5, §7.9, §8.2, §9.8. `LogLevel` includes `UNKNOWN` (C7 §3.3). `EnabledAction` matches C4, and `test_catalogue_alignment.py` keeps the two in step.
- C9 `PushPayload` matches §9.7 exactly; C3 messages and event data match §9.4 (`hello.server_time` gives the app its clock for expiry countdowns, MOB-007).
- C5 `captured` names (`previous_replicas`, `previous_release`) match C4 `captures`; `dry_run`/`execute` field rules match §9.5.
- TB-010: `DeployListItem` leaves out `image_tag`, so `chaos-shop/...` never reaches the agent. The only fault-like words in the fixtures are `root_cause_category: bad_deploy` (the agent's own taxonomy, §6.5) and a test note in `signing/vectors.json`.
- `ContainerHealth.release` accepts the C7 release labels of third-party images (`17.11`, `7.4.11`, `1.30.5`).
- The Dart fixture test (`mobile/test/contracts/fixtures_test.dart`) parses all 3 timelines, all 14 WebSocket messages and the ledger fixture from this branch: 21/21.

## Issues

| # | File:line | Issue | Suggested fix |
|---|---|---|---|
| 1 | `python/oncallpilot_contracts/ws.py:109` (`EvidenceAddedData`), `ws-protocol.md:59` | "Payload cut to 4 KB" is not defined, and `EvidenceAddedData` inherits `Evidence`'s per-kind payload validation, so a payload that is literally cut no longer validates. | Define the cut: drop trailing list items (`lines`, `points`, `records`, `chunks`, `containers`) until the JSON is ≤ 4 KB, so the shape stays valid, then set `payload_truncated: true`. Add a test with a 50-line `log_query` payload. |
| 2 | `python/oncallpilot_contracts/domain.py:180` (`DeployListItem`) vs C6 `ledger.py:40–42` (on `tanzeel`) | `get_recent_deploys` drops `reason` and `replicas`. C6 lists `reason` as agent-visible, and without `replicas` a `kind=scale` record says nothing about the scale. | Add `replicas: int (1–5)` and `reason: str (1–200)` to `DeployListItem`; keep `image_tag` and `deploy_id` out. |
| 3 | `python/oncallpilot_contracts/domain.py:170` (`MetricQueryPayload.service`) vs C7 §2.1 | `service: ServiceName` allows `lb`, `payments`, `redis`, `postgres`, but only `api` and `worker` expose metrics (C7 §2.1); `upstream_latency_p95` is measured on `api`. | Use `Literal["api", "worker"]`, or document that other services always return an empty series with `note`. |
| 4 | `python/oncallpilot_contracts/domain.py:166` (`MetricQueryPayload`) | The app's metric card (MOB-005, `fl_chart`) needs an axis unit; the payload has none. | Add `unit: Literal["ratio", "seconds", "bytes", "requests_per_second", "count"]`, set by the tool from the template; or put a template → unit table in the C1 docstring for the app to copy. |
| 5 | `python/oncallpilot_contracts/domain.py:649–661` (`MonitorSettings.locked`) | MOB-013 shows "read-only fields with the lock reason", but there is only `locked: bool`. | Add `lock_reason: str \| None` (e.g. "Benchmark lock is on"), or agree that the app shows a fixed text and say so in the docstring. |
| 6 | `fixtures/timelines/bad_deploy_success.json:155, 165, 787, 797` | Commit SHAs and messages differ from the testbed (`chaos-shop/releases.yaml`: 1.5.0 = `e56a282e7523…` "checkout: compute totals with new pricing rounding", 1.4.0 = `606c2674b8e3…` "cart: cache price lookups"). | Copy the values from `releases.yaml`, so mock mode, recorded fixtures (B2.6) and the live ledger agree. (Tanzeel's own `fixtures/ledger/deploys.jsonl` also predates `releases.yaml`; aligning it is a separate contract commit, proposed to Tanzeel.) |
| 7 | `fixtures/timelines/bad_deploy_success.json:46, 58` | The fixture's 1.5.0 failure is `KeyError: 'unit_price'`; the real release 1.5.0 raises `IndexError: tuple index out of range` for orders of 50.00 or more (A1.3). | Use the real exception type and message, so the mock matches the live scenario. |
| 8 | `fixtures/` | No fixture yet for the app widget tests of MOB-019 (scenario 8, `suspicious_content: true`), MOB-011 (escalated `runner_timeout`, no rollback button), MOB-012 (an audit page) and MOB-017 (error envelopes for each approve and challenge code). | Not a Phase 0 blocker (S0.4 asks for 3 timelines). Add them in a later contract PR before A2/A3 need them. |

## Issues found while building the runner (A2.1, 2026-10-10)

Read from `origin/usman` at `0582f37`. The signing vector itself is correct: every `canonical` string matches its message, and each signature verifies or fails exactly as labelled.

| # | File:line | Issue | Suggested fix |
|---|---|---|---|
| 9 | `python/oncallpilot_contracts/runner.py` (docstring, check 1) | A `BAD_SIGNATURE` refusal echoes `request_id` and `execution_id` from a message that failed verification. A forged message with a real `execution_id` makes the runner sign a refusal; the worker then fails that execution (`runner_refused`). If the genuine execute arrives afterwards it still runs, because the refusal does not consume the idempotency key, while the incident already shows `escalated`. The runner follows C5 for now (ruling 2026-10-10). | Either the worker ignores a refusal for an execution it has not seen fail its own checks, or a refused `execute` also sets `ocp:runner:idem:{execution_id}` so a later message with that id is a duplicate. Either way, write the rule into C5. |
| 10 | `runner.py` (`RunnerResult`) | A message without a usable `type`, a UUID `request_id`, or (for `execute`) a UUID `execution_id` cannot be answered with a valid `RunnerResult`. The runner logs and acknowledges it with no result, and the worker's sweeper ends a real execution with `runner_timeout`. | State in C5 that such messages get no result. |
| 11 | `runner.py` (`RunnerRequest.catalogue_version`) | C5 does not say what the runner does when `catalogue_version` differs from the catalogue it loaded. The runner does not compare them yet. | Decide: refuse with `VALIDATION_ERROR` (fail closed), or accept and log. |

## For information

- **C7 change on `tanzeel`:** logger names are now `shop.<area>` (`shop.access`, `shop.checkout`, …) instead of `chaosshop.*` (TB-010; `logger` is agent-visible). Nothing on `origin/usman` refers to `chaosshop`, so no contract file needs changing; Loki and Grafana queries must filter on `shop.*`.
