# Context — current state

> Rewritten at the end of every session so it matches reality (`CLAUDE.md` §11.4). Read it right after `CLAUDE.md`, then read `handoff.md`.
> **Last updated:** 2026-10-09 (session 1, in progress)

## Who and where
- This checkout is **Usman's** (Stream B: backend, AI agent, database, observability, benchmark). Teammate: **Tanzeel** (Stream A: Flutter app, Chaos Shop testbed, runner).
- Repository: `https://github.com/f243077-cell/OneCallPilot` · local folder `H:\projects\OneCallPilot`.

## Branches
| Branch | Owner | What is on it |
|---|---|---|
| `main` | both | Only the initial README. Nothing is merged yet. |
| `tanzeel` | Tanzeel | Phase 0 for Stream A plus A1.1: repo skeleton, CI, contracts C4/C6/C7, Flutter project, Chaos Shop baseline, proxy and network spikes. Open as draft PR #1 (not to be merged as is). |
| `usman` | Usman | Started from `tanzeel` at `6cac236`, then Usman's work on top. The only branch this checkout pushes (a local pre-push hook enforces it). |

## Phase
Phase 0 (kickoff and contracts), week 1. Phase 1 starts once all contracts C1–C9 are merged and tagged `contracts-v0.1.0`.

## Usman's Phase 0 tasks
| Task | Status |
|---|---|
| B0.1 Supabase projects `ocp-dev`, `ocp-bench` | not started (Usman, by hand) |
| B0.2 Draft contracts C1, C2, C3, C5, C8, C9 | in progress |
| B0.3 LLM and embedding API keys | not started (Usman, by hand) |
| S0.6 Docker Desktop on Usman's laptop | not started (Usman, by hand) |
| S0.7 (a) socket-proxy-ro + Alloy, (c) embeddings, (d) Supabase JWKS | blocked on S0.6, B0.3, B0.1 |

## Machine (Usman's laptop)
- Windows 10 Home; git 2.50 (`user.name` Usman, Git Credential Manager).
- Python 3.13 only. `uv`: not installed yet. Docker: not installed. Flutter: not installed (not needed for Stream B).

## Decisions so far
- `usman` is based on `tanzeel`, so both share history; Tanzeel's new work comes in with `git merge origin/tanzeel` (never rebase `usman`).
- Session notes (`context.md`, `handoff.md`, `learn.md`) are committed and pushed with the work.
