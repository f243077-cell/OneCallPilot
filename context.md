# Context — current state

> Rewritten at the end of every session so it matches reality (`CLAUDE.md` §11.4). Read it right after `CLAUDE.md`, then read `handoff.md`.
> **Last updated:** 2026-10-09, end of session 1.

## Who and where
- This checkout is **Usman's** (Stream B: backend, AI agent, database, observability, benchmark). Teammate: **Tanzeel** (Stream A: Flutter app, Chaos Shop testbed, runner).
- Repository: `https://github.com/f243077-cell/OneCallPilot` · local folder `H:\projects\OneCallPilot`.

## Branches
| Branch | Owner | What is on it |
|---|---|---|
| `main` | both | Only the initial README. Nothing is merged yet. |
| `tanzeel` | Tanzeel | Phase 0 Stream A plus A1.1, up to `6cac236`: skeleton, CI, contracts C4/C6/C7, Flutter project, Chaos Shop baseline, rw-proxy and network spikes. Draft PR #1 (not to be merged as is). |
| `usman` | Usman | `tanzeel` at `6cac236`, plus Usman's 11 commits: `CLAUDE.md` §11 and the session files, contracts C1/C2/C3/C5/C8/C9 and the Phase 0 fixtures (7 commits, only `contracts/`), socket-proxy-ro, and the week-1 progress note. Pushed to `origin/usman`. |

A local `.git/hooks/pre-push` (not in git) refuses every push except `refs/heads/usman`.

## Phase
**Phase 0 (contracts), week 1.** All of Usman's contract drafts (B0.2) and the Phase 0 fixtures (S0.4) are done. They are waiting for Tanzeel's read and the 30-minute fixture walkthrough. After that: `contract/*` PRs → merge → tag `contracts-v0.1.0` → Phase 1, starting with B1.1 (migrations).

## Usman's Phase 0 tasks
| Task | Status |
|---|---|
| B0.2 Draft contracts C1, C2, C3, C5, C8, C9 | **drafted** on `usman`; contract gate green (246 tests) |
| S0.4 Phase 0 fixtures (timelines, ws, signing vectors) | **drafted** on `usman` |
| Reader of C4, C6, C7 | **read**: nothing blocking (notes in `handoff.md`) |
| socket-proxy-ro in `observability.yml` (Tanzeel's ask) | **committed**, not run yet (no Docker) |
| S0.6 Docker Desktop on Usman's laptop | not started (Usman, by hand) |
| B0.1 Supabase `ocp-dev`, `ocp-bench` | not started (Usman, by hand) |
| B0.3 LLM and embedding API keys | not started (Usman, by hand) |
| S0.7 (a) ro proxy + Alloy · (c) embeddings · (d) Supabase JWKS | blocked on S0.6 · B0.3 · B0.1 |

## Where things are (contracts)
| Contract | Files |
|---|---|
| C1 domain models | `contracts/python/oncallpilot_contracts/{common,enums,domain}.py` |
| C2 REST API | `contracts/openapi.yaml` + `api.py` (bodies) |
| C3 WebSocket | `ws.py`, `contracts/ws-protocol.md`, `contracts/fixtures/ws/` |
| C5 runner messaging | `runner.py`, `signing.py`, `contracts/fixtures/signing/vectors.json` |
| C8 approval protocol | `approval.py`, `contracts/approval-protocol.md` |
| C9 push payload | `push.py`, `contracts/push.md` |
| Phase 0 timelines | `timeline.py`, `contracts/fixtures/timelines/` |
| Generated JSON Schemas | `contracts/schemas/*.json` (41; regenerate, never edit) |

## How to run the contract checks
From `contracts/python`: `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy oncallpilot_contracts tests`, `uv run pytest -q`, then `uv run python -m oncallpilot_contracts.schema_export` followed by `git diff --exit-code -- ../schemas`. This is the same sequence as `.github/workflows/contracts.yml`.

## Machine (Usman's laptop)
- Windows 10 Home; git 2.50 (`user.name` Usman, Git Credential Manager signed in to GitHub; pushing `usman` works).
- `uv` 0.12.7, installed with pip into the system Python 3.13. `uv` manages Python 3.12.14 for the contracts environment (`contracts/python/.venv`, git-ignored).
- **No Docker yet**, and no Flutter (not needed for Stream B). The internet connection is slow (about 370 kB/s), so downloads take minutes.

## Decisions made (and where they are written)
- `usman` is based on `tanzeel`; sync by merging `origin/tanzeel`, never rebase (`CLAUDE.md` §11).
- Session notes are committed and pushed (`CLAUDE.md` §11.4).
- Schema file names are snake_case (`incident_detail.json`), like Tanzeel's `deploy_record.json`. API-005 says `IncidentDetail.json`, and that mismatch is flagged to Tanzeel.
- Runner messages: stream field `msg`; HMAC over the canonical JSON of the message **as sent**; keys hex-encoded in env vars and at least 32 bytes; `execution_result` carries `captured` for the C4 rollback step (`runner.py`, `signing.py`).
- `proposal.fingerprint` = sha256 of the canonical JSON of `{proposal_id, action, params, risk_tier, state_fingerprint, dry_run_at}` (`approval-protocol.md` §2).
- WebSocket `hello` is sent only after a successful `auth` (`ws-protocol.md` §3).
- After a failed signal verification, the original proposal stays `succeeded` (the runner did its part). The failure shows in `executions.health_after.verdict = fail` and incident `action_failed` (`scale_failed_rollback` timeline). This needs confirming in the walkthrough.
