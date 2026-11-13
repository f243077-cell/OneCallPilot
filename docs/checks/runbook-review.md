# Runbook review (`runbooks/`, 8 files) — 2026-11-04

> Review of the corpus that `search_runbooks` returns to the agent (k ≤ 3). Reviewed in a Claude Code session for Tanzeel; Tanzeel and Usman each confirm below (the runbooks are cross-reviewed, `CLAUDE.md` §3).
> Runbooks are agent input: a change after `bench-freeze` is forbidden (`CLAUDE.md` §6.14), and before it, any change needs the runbook tests (`backend/tests/test_runbook_corpus.py`, `chaos-shop/cli/tests/test_naming.py`).

## Checked
| Check | Result |
|---|---|
| Actions, risk tiers and approval types match `contracts/actions.yaml` (`restart_service` low/tap, `scale_service` low/tap with 1–5 replicas, `rollback_deploy` medium/biometric, `clear_cache` medium/biometric; `run_migration_rollback` never mentioned as available) | **match** |
| Rollback and undo text matches the catalogue's `rollback_step` (rollback to the replaced release; scale back to `previous_replicas`) | **match** |
| Every signal a runbook asks for exists as a tool or metric template (`memory_rss`, `cpu_usage`, `db_pool_in_use`, `db_pool_wait_p95`, `cache_errors`, `upstream_latency_p95`, deploy history, container health, logs) | **match**, one soft spot below |
| No scenario names, benchmark answers, release numbers or testbed container names (TB-010; benchmark integrity, `CLAUDE.md` §5.8) | **none found** |
| Each runbook has a disproof check (AI-011 reflection) | **yes**, 6 of 6 action runbooks |
| Escalation is presented as a correct outcome (`when-to-escalate.md`, `slow-dependency.md`, `reading-logs-safely.md`) | **yes** |
| Untrusted log text: treated as data, never as a step (`reading-logs-safely.md`, SEC-005) | **yes** |
| Nothing tells the reader to run commands, shells, or anything outside the catalogue (`CLAUDE.md` §6.1) | **none found** |

Each of the 8 scenarios is covered by generic guidance, without naming it: memory growth and pool exhaustion → restart; stopped cache → restart; failed release and start-up crash after a release → rollback; surge → scale; slow third party → escalate; instruction-like log text → treat as data.

## Findings (none blocks the freeze)
1. `scale-for-load.md`, "Per-request cost is unchanged: the latency of a single request at low load…": the agent has no tool for single-request latency at low load. Harmless (it can use `db_pool_wait_p95` and `upstream_latency_p95` instead), but the sentence could name those.
2. `slow-dependency.md` gives "a payment provider" as an example. That is a common real-world example, not a benchmark answer, and the TB-010 word check passes. Keep it.

## Decision
- [ ] Tanzeel: confirmed / changes wanted
- [x] Usman: confirmed (2026-11-13)
