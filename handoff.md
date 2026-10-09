# Handoff — session log

> One dated entry per session, **newest first**, each with four parts: **Done** (with commits), **Next** (in order), **Waiting on** (Usman or Tanzeel), and **Message for Tanzeel** (when there is one). Old entries are never edited (`CLAUDE.md` §11.4).

## 2026-10-09 — Session 1: branch setup (in progress)

### Done
- Turned `H:\projects\OneCallPilot` into a git checkout of the new `usman` branch, started from `origin/tanzeel` (`6cac236`). The five loose copies of the docs were older than Tanzeel's (they still said 20 GB); they were byte-identical to commit `91dddda8`, so nothing was lost by replacing them.
- Added a local pre-push hook that only allows pushing `usman`.
- Added `CLAUDE.md` §11 (Usman's working rules) and these three session files.

### Next
- Install `uv`, then draft contracts C1, C2, C3, C5, C8, C9 and the Phase 0 fixtures.
- Add `socket-proxy-ro` (`infrastructure/compose/observability.yml`) and its `include:` line.

### Waiting on
- Nothing yet.
