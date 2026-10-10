# Context — current state

> Rewritten at the end of every session so it matches reality (`CLAUDE.md` §11.4). Read it right after `CLAUDE.md`, then `handoff.md`, then **`next.md`**, the step-by-step plan for the next session.
> **Last updated:** 2026-10-10, end of session 2.

## Who and where
- This checkout is **Usman's** (Stream B: backend, AI agent, database, observability, benchmark). Teammate: **Tanzeel** (Stream A: Flutter app, Chaos Shop testbed, runner).
- Repository: `https://github.com/f243077-cell/OneCallPilot` · local folder `H:\projects\OneCallPilot`.

## Branches
| Branch | Owner | What is on it |
|---|---|---|
| `main` | both | Only the initial README. Nothing is merged yet. |
| `tanzeel` | Tanzeel | Up to `1a4f03a`: Stream A Phase 0, A1.1–A1.6, A2.1 runner verification, `ocp up/down/doctor`, Gate G1 checks, the Dart fixture test. Draft PR #1. CI: 5 jobs green, **chaos-shop red** (cause not yet known). |
| `usman` | Usman | **All of `tanzeel` up to `1a4f03a`, merged** (`d5c49a0`), plus Usman's work: contracts C1/C2/C3/C5/C8/C9 with the review fixes, socket-proxy-ro, `contract-review-b.md`, the chaos CLI lock refresh, and the session notes. Pushed; head `ae54f2f` before the notes commit. |

A local `.git/hooks/pre-push` (not in git) refuses every push except `refs/heads/usman`.

## Phase
**Phase 0, closing.**
- Both reviews are done in writing; Tanzeel said a walkthrough isn't needed.
- All contracts are drafted and reviewed: C4, C6, and C7 approved (C7 needs his deviations written in), and every C1/C2/C3/C5/C8/C9 issue fixed or accepted.
- The Dart fixture test exists on Tanzeel's side.
- **Waiting on Tanzeel's go-ahead** to merge into `main`: contracts through `contract/*` PRs, the rest through PRs, each merged with green CI. Then the `contracts-v0.1.0` tag, then Phase 1.

## Usman's open Phase 0 tasks
| Task | Status |
|---|---|
| S0.6 Docker Desktop on Usman's laptop | not started (Usman, by hand) |
| B0.1 Supabase `ocp-dev`, `ocp-bench` | not started (Usman, by hand) |
| B0.3 LLM and embedding API keys | not started (Usman, by hand) |
| S0.7 (a) ro proxy + Alloy · (c) embeddings · (d) Supabase JWKS | blocked on S0.6 · B0.3 · B0.1 |
| C8-issue fixtures (MOB-019, MOB-011, MOB-012, MOB-017) | accepted for a later contract PR |

## Where things are (contracts)
| Contract | Files |
|---|---|
| C1 domain models | `contracts/python/oncallpilot_contracts/{common,enums,domain}.py` |
| C2 REST API | `contracts/openapi.yaml` + `api.py` |
| C3 WebSocket | `ws.py` (incl. `cut_evidence_payload`), `contracts/ws-protocol.md`, `contracts/fixtures/ws/` |
| C5 runner messaging | `runner.py`, `signing.py`, `contracts/fixtures/signing/vectors.json` |
| C8 approval protocol | `approval.py`, `contracts/approval-protocol.md` |
| C9 push payload | `push.py`, `contracts/push.md` |
| Phase 0 timelines | `timeline.py`, `contracts/fixtures/timelines/` |
| Reviews | `docs/checks/contract-review-a.md` (Tanzeel on Usman's), `docs/checks/contract-review-b.md` (Usman on Tanzeel's, plus replies) |

## How to run the checks
- Contracts, from `contracts/python`: `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy oncallpilot_contracts tests`, `uv run pytest -q` (259 tests), then `uv run python -m oncallpilot_contracts.schema_export` and `git diff --exit-code -- ../schemas`.
- Every lockfile: run `uv lock --check` in `contracts/python`, `chaos-shop`, `chaos-shop/cli`, `runner`, and `tools/ocp`. A change to the contracts package's dependencies also changes `chaos-shop/cli/uv.lock`, because that CLI depends on contracts by path.

## Machine (Usman's laptop)
- Windows 10 Home; git 2.50 (Git Credential Manager; pushing `usman` works).
- `uv` 0.12.7 (pip, system Python 3.13); `uv` manages Python 3.12 for each package's `.venv`.
- **No Docker yet**, no Flutter, no `gh` CLI. GitHub job logs need admin rights; check-run status is readable through the public API.

## Decisions made (and where they are written)
- `usman` merges `origin/tanzeel`, never rebases (`CLAUDE.md` §11).
- **Nothing goes to `main` until Tanzeel says go, and then only through PRs** (Usman's decision, 2026-10-10). §11.1 has to be relaxed then, to allow the PR branches; ask Usman.
- Schema files are snake_case; the runner stream field is `msg`; HMAC is computed over the JSON as sent; keys are hex (`runner.py`, `signing.py`).
- `proposal.fingerprint` formula: `approval-protocol.md` §2. WS `hello` comes after auth.
- `evidence.added` payloads over 4,096 bytes are cut by dropping list items from the end (`ws-protocol.md` §4.1).
- Runner: a refused or aborted `execute` claims its idempotency key; unanswerable messages get no result; a different `catalogue_version` is refused (`runner.py`).
- Metric payloads carry `unit` from `METRIC_UNITS`.
