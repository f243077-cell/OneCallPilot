# Contract review (Stream B) — Tanzeel's C4, C6, C7, and replies to review A

> **Reviewer:** Usman (Stream B) · **Date:** 2026-10-10 · **Reviewed:** `origin/tanzeel` at `1a4f03a`, merged into `usman` as `d5c49a0` · **Against:** `docs/architecture.md`, `docs/requirements.md`, and C1/C2/C3/C5/C8/C9 on `usman`.
> Replies to `docs/checks/contract-review-a.md` are in §2. All fixes are on `usman`, each in a commit that touches only `contracts/`.

## 1. C4, C6, C7

| Contract | Verdict | Notes |
|---|---|---|
| **C4** `contracts/actions.yaml` | **Approved** | Unchanged since the first read. `captures` names match C5 `captured`; `test_catalogue_alignment.py` keeps `EnabledAction` equal to the enabled set. |
| **C6** `ledger.py`, `fixtures/ledger/deploys.jsonl` | **Approved** | The fixture now uses the testbed's release history (`chaos-shop/releases.yaml`); C1's timelines use the same values (issue 6). |
| **C7** `contracts/telemetry.md` | **Approved, with one change** | Please write the two accepted deviations from `docs/checks/week-1.md` ("C7 deviations") into C7 in your C7 contract PR: (a) no access-log line for `/metrics` and `/internal/*` (cs-api, cs-payments, and `/internal/*` in cs-lb), which makes §3.2's "one access line per request" say "per request, except `/metrics` and `/internal/*`"; (b) cs-payments request lines carry no `route` field (§3.3). Both are fine for Stream B; this only makes the document match the code. The `shop.<area>` logger rename is fine: nothing on Stream B filters on `logger`. |

## 2. Replies to contract-review-a.md

| # | Reply | Where |
|---|---|---|
| 1 | **Fixed.** The cut is defined: items are dropped from the end of the kind's list (`lines`, `points`, `records`, `containers`, `chunks`; `signals` keeps at least one) until the payload is at most 4,096 bytes of compact UTF-8 JSON, so it still validates. `cut_evidence_payload()` implements it; there is a test with a 50-line `log_query` payload. | `aa91cd9` · `ws.py`, `ws-protocol.md` §4.1 |
| 2 | **Fixed.** `DeployListItem` has `replicas` (1–5) and `reason` (1–200); `deploy_id` and `image_tag` stay out. | `3e33a3e` · `domain.py` |
| 3 | **Fixed.** `MetricQueryPayload.service` is `Literal["api", "worker"]`. | `3e33a3e` |
| 4 | **Fixed.** `unit` ∈ `ratio`, `seconds`, `bytes`, `cores`, `requests_per_second`, `per_second`, `count`; it must equal `METRIC_UNITS[template]`, and `METRIC_UNITS` is the table for the app. I added `cores` (for `cpu_usage`) and `per_second` (for `cache_errors`) to your list. | `3e33a3e` |
| 5 | **Fixed.** `MonitorSettings.lock_reason: str \| None`, set exactly when `locked` is true. | `3e33a3e` |
| 6 | **Fixed.** Timelines and samples use the `releases.yaml` SHAs, commit messages, and config hash. | `3e33a3e` |
| 7 | **Fixed.** Scenario 2 evidence shows `IndexError: tuple index out of range` from "unhandled error while serving the request", as release 1.5.0 logs it. Evidence summaries also use relative times ("90 s before the errors") instead of clock times, as you suggested, so they stay true when mock mode shifts timestamps. | `3e33a3e` |
| 8 | **Accepted, later.** The extra fixtures for MOB-019, MOB-011 (`runner_timeout`), MOB-012, and MOB-017 come in a separate contract PR before A2/A3 need them. Tell me which you need first. | — |
| 9 | **Fixed (rule in C5).** Every `refused` or `aborted` answer to an `execute` first claims `ocp:runner:idem:{execution_id}` with `SET NX`. A forged message that borrows a real `execution_id` therefore leaves the genuine `execute` as a duplicate that never runs, which matches the incident's `escalated` (`runner_refused`) state. No requirement changes. The runner needs this in its refusal path. | `857b775` · `runner.py` docstring |
| 10 | **Fixed (rule in C5).** Such a message gets no result: it is logged and acknowledged, and the worker's sweeper ends a real execution with `RUNNER_TIMEOUT` (FR-024). | `857b775` |
| 11 | **Fixed (rule in C5).** A different `catalogue_version` is refused with `VALIDATION_ERROR` (fail closed); it is check 4, before the action check. | `857b775` |

After these fixes the contract gate is green: ruff, `mypy --strict`, 259 tests, and schemas that regenerate with no diff. The new fields (`unit`, `replicas`, `reason`, `lock_reason`) are additions; your Dart DTOs ignore unknown keys, so the fixture test keeps passing until you use them.

## 3. Answers to the other notes

- **`chaos_net`:** Stream B's compose files will name it plainly (`chaos_net: {}`), never `external: true`.
- **Deploy ledger:** `copilot.yml` will declare `deploy_ledger: {}`, mount it read-only at `/ledger`, and set `LEDGER_PATH=/ledger/deploys.jsonl` for backend-worker (B2.2).
- **OOMKilled:** noted. `get_service_health` (B2.2) and the detector use `restart_count` as the signal. `ContainerHealth.oom_killed` stays in the shape, but it is only true until Docker restarts the container.
- **Scenario 5:** noted for the detector (B1.7): it is detected by `p95_latency`, not `error_rate`.
- **`ocp` command line:** I have no existing structure, so `tools/ocp` is the single entry point. I'll add `seed-incident` (and later `smoke`) as subcommands there.
- **Runbooks (S1.2):** your split is fine. Format: one Markdown file per topic in `runbooks/<topic>.md` (lower-case, hyphens). One `#` title, then `##` sections; `search_runbooks` chunks on headings first (about 500 tokens, AI-026). Write generic operations docs with no scenario names or answers (TB-010). We review each other's.
