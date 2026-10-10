# Next session — start here

> **For:** the next Claude Code session working for **Usman** in this checkout.
> **Written:** 2026-10-10, at the end of session 2.
> **Read order:** `CLAUDE.md` (§11 holds Usman's rules), `context.md`, `handoff.md`, then this file.
> **When the session ends,** rewrite this file with the steps for the session after it.

## 0. Rules (short form of `CLAUDE.md` §11)
- Commit only to `usman`, and push only with `git push origin usman`; the pre-push hook refuses anything else, and must never be bypassed with `--no-verify`.
- **Never push to `main`**, even though Usman's goal is to get Phase 0 into `main`. The agreed route is in §2.
- Merge `origin/tanzeel`; never rebase. A contract change is a commit that touches only `contracts/`.
- No attribution lines; write multi-line commit messages with a bash heredoc; run the secrets check before each push; never print a key or token.

## 1. Start of the session
1. `git status` should be clean. Then `git fetch origin` and `git log --oneline usman..origin/tanzeel`. If there is anything new, first test the merge with `git merge-tree --write-tree --name-only usman origin/tanzeel`, then merge.
2. Run the contract gate (259 tests on 2026-10-10) and `uv lock --check` in every package (`context.md`, "How to run the checks").
3. Look for Tanzeel's answers:
   - new commits, `docs/checks/*.md` on `tanzeel`, comments on PR #1;
   - CI status through the public API: `curl -s https://api.github.com/repos/f243077-cell/OneCallPilot/commits/<sha>/check-runs`.
   - Ask Usman whether Tanzeel has said "go".

## 2. If Tanzeel has said "go": merge Phase 0 into `main` through PRs
Usman decided on 2026-10-10: nothing is pushed to `main` directly; everything goes through PRs, and each is merged only when CI is green.

1. **Ask Usman to relax `CLAUDE.md` §11.1.** The `contract/*` PR branches have to be pushed, and today the hook refuses every branch except `usman`. Agree the exact branch names and the hook change first.
2. **Check the preconditions:**
   - Tanzeel's chaos-shop CI job is green.
   - He has written the C7 deviations into `telemetry.md`.
   - He has added the C5 issue-9 rule to the runner.
3. **Plan the PRs with Tanzeel** so that `main` never sees a conflict:
   - the contracts first: one `contract/*` PR per contract, or one PR for v0.1;
   - include the `chaos-shop/cli/uv.lock` refresh with the contracts;
   - then each owner's implementation PRs;
   - then the tag `contracts-v0.1.0`.
   - **Watch out:** if contracts are squash-merged into `main` and the full branches are merged later, git can report add/add conflicts on files that changed again after the squash. Agree one route and keep the branches rebased on `main` afterwards (§8.1 allows that for short-lived branches).
4. Open each PR only with Usman's go-ahead. There is no `gh` CLI here, so Usman may need to open PRs in the web UI.

## 3. If not yet: other work
- **Tanzeel's issue 8 fixtures** (MOB-019 suspicious evidence, MOB-011 `runner_timeout`, MOB-012 audit page, MOB-017 error envelopes): build them once he says which comes first, as one contract commit. The fixture generators exist only in session 1's scratchpad (`gen_timelines.py`, `gen_ws.py`, `gen_vectors.py`) and may be gone; ask Usman whether to commit a generator.
- **S0.7 (a), (c), (d)**, once Usman has Docker, a Gemini key, and Supabase. The detailed steps are in the session 1 version of this file, kept in git history: `git show 0582f37:next.md`, §3.2–§3.4.
- If everything else is blocked, ask Usman whether to start **C10** (benchmark records).

## 4. Notes for later Stream B tasks
- **B1.5:**
  - Prometheus jobs need `honor_labels: true`.
  - Name `chaos_net` plainly, as `chaos_net: {}`, never `external: true`.
  - Loki and Grafana filter on `shop.*` loggers, if they filter on loggers at all.
- **B1.7:** scenario 5 is detected by `p95_latency`, not `error_rate`.
- **B1.8:** the `query_logs` level `ERROR` should match `ERROR|CRITICAL`.
- **B2.2:**
  - `get_service_health` uses `restart_count`, because `OOMKilled` is cleared on restart.
  - Ledger: the volume `deploy_ledger` (`deploy_ledger: {}` in `copilot.yml`), mounted read-only at `/ledger`, with `LEDGER_PATH=/ledger/deploys.jsonl`; the files belong to uid 10001.
- **B1.4:** `ocp seed-incident` goes into Tanzeel's `tools/ocp`, the single CLI.
- **B2.10:** RUN-015's audit entry for a dropped runner result has no event name in §7.9.

## 5. The session is done when
Tanzeel's latest work is merged; the gate is green; any PR work was agreed with Usman first; the session files are updated (a new dated `handoff.md` entry; `context.md` and this file rewritten; `learn.md` appended); and everything is pushed to `usman`.
