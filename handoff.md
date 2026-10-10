# Handoff — session log

> One dated entry per session, **newest first**, each with four parts: **Done** (with commits), **Next** (in order), **Waiting on** (Usman or Tanzeel), and **Message for Tanzeel** (when there is one). Old entries are never edited (`CLAUDE.md` §11.4).

## 2026-10-10 — Session 3: PR branches allowed, Docker check, Phase 0 status

### Done
- **Merged `origin/tanzeel`** (`0ca3034`): one new commit, `b71ed6b`, runner coverage measured with pytest-cov (CI enforces 85 %). Clean merge.
- **Relaxed `CLAUDE.md` §11.1, as Usman approved** (`406a8bb`). Besides `usman`, his PR branches from §8.1 (`contract/*`, `feat/b-*`, `fix/*`, `chore/*`, `docs/*`, `test/*`, `bench/*`) may now be pushed. Direct pushes or merges to `main` or `tanzeel`, `feat/a-*`, and tags stay forbidden; a PR is opened only when Usman asks, and for Phase 0 only after Tanzeel's go. The local pre-push hook was updated to match and tested against each kind of target.
- **Checked Docker.** Docker Desktop is installed (client 29.8.2, Compose v5.5.1, in `%LOCALAPPDATA%\Programs\DockerDesktop`), but **the engine is not running.** WSL 2 is not installed (`wsl.exe` offers only `--install`), and Docker Desktop on Windows Home needs it. Its `bin` folder is also not on this terminal's PATH yet.
- **CI on `tanzeel`:** the `chaos-shop` job is still red on his newest commit `b71ed6b`; the other five jobs are green.

### Phase 0 status (definition of done, `phases.md` §5)
| Item | Status |
|---|---|
| All contracts C1–C9 read by the other developer | ✅ both written reviews done (`contract-review-a.md`, `contract-review-b.md`); a written review replaced the walkthrough, as Tanzeel decided |
| All contracts merged into `main`, tag `contracts-v0.1.0` | ❌ waits for Tanzeel's go, his red chaos-shop job, and the PRs |
| Python fixtures validate against the schemas | ✅ 259 contract tests |
| Dart parses the fixtures | ✅ Tanzeel's `mobile/test/contracts/fixtures_test.dart`; mobile CI green |
| Phone reached the laptop (S0.5) | ✅ |
| CI skeleton green | ⚠️ 5 of 6 jobs green; chaos-shop red (Tanzeel) |
| S0.6 Docker Desktop on Usman's laptop | ⚠️ installed, not running: needs WSL 2 |
| B0.1 Supabase projects · B0.3 API keys | ❌ Usman, by hand |
| S0.7 (a) ro proxy + Alloy · (c) embeddings · (d) Supabase JWKS | ❌ need Docker · the Gemini key · Supabase |

**Phase 0 is not complete.** What closes it: WSL 2 → Docker running → S0.7 (a); the Gemini key → S0.7 (c); Supabase → S0.7 (d); Tanzeel fixes chaos-shop CI and says go → PRs into `main` → tag `contracts-v0.1.0`.

### Next
**`next.md` has the step-by-step version.**
1. **Usman:** install WSL 2 and start Docker Desktop (steps below). Then get a Gemini key and create the Supabase projects (the steps in the session 1 entry).
2. **Next session:** run S0.7 (a) once `docker version` shows a server; (c) and (d) once the key and Supabase exist.
3. **When Tanzeel says go:** open the Phase 0 PRs into `main` as `next.md` §2 says.

### Waiting on
- **Usman:** WSL 2 + Docker Desktop running; the Gemini key; Supabase.
- **Tanzeel:**
  - the red chaos-shop CI job;
  - his go-ahead for the merge;
  - the C7 deviations written into `telemetry.md`;
  - the C5 issue-9 rule in the runner;
  - the `chaos-shop/cli/uv.lock` refresh in the contracts PR.

### Usman's steps: get Docker running (Windows 10 Home)
1. Open PowerShell **as Administrator** and run `wsl --install --no-distribution`. Restart the laptop when it finishes.
2. Start **Docker Desktop** from the Start menu. Accept the licence and choose the WSL 2 backend if asked, then wait until it shows "Engine running".
3. Open a **new** terminal (so PATH picks up Docker) and check:
   - `docker version` shows a *Server* section;
   - `docker compose version` shows v2.20 or newer;
   - `docker run --rm hello-world` prints its greeting.
4. In the repository root, copy `.env.example` to `.env` and replace the four testbed values with your own random hex (`python -c "import secrets; print(secrets.token_hex(24))"`).
5. Tell Claude "Docker is running".

### Message for Tanzeel
> Quick update from my side: I merged your `b71ed6b` (clean). Your **chaos-shop CI job is still red** on it, the same as on `1a4f03a`; the other five are green. Can you check that log? Once it's green and you say go, I'll open my contract PRs into `main`. My rules now allow PR branches (`contract/*` and so on); nothing goes to `main` directly.

## 2026-10-10 — Session 2: merged Tanzeel's work, answered his contract review

### Done
- **Merged `origin/tanzeel` into `usman`** (`d5c49a0`): 44 commits (A1.2–A1.6, A2.1 runner, `ocp up/down/doctor`, Gate G1 checks, the Dart fixture test). There were no conflicts; the contract gate stayed green.
- **Reviewed C4, C6, and C7:** C4 and C6 approved; C7 approved, provided Tanzeel writes his two accepted deviations into `telemetry.md`. See `docs/checks/contract-review-b.md` (`f27d37d`).
- **Answered all 11 issues** of `docs/checks/contract-review-a.md`. Ten are fixed and issue 8 is accepted for later; the per-issue table is in `contract-review-b.md`.

  | Commit | Fix |
  |---|---|
  | `3e33a3e` | C1: `DeployListItem` gets `replicas` and `reason`; metric `service` is `api`/`worker` only; a new `unit` (`METRIC_UNITS`); `MonitorSettings.lock_reason`; timelines use the testbed's real release history and the real 1.5.0 `IndexError`, with relative-time summaries |
  | `aa91cd9` | C3: the 4 KB cut of `evidence.added` payloads is defined (`cut_evidence_payload()`, `ws-protocol.md` §4.1) |
  | `857b775` | C5: a refused or aborted `execute` claims its idempotency key; unanswerable messages get no result; a different `catalogue_version` is refused |

- **Fixed a CI break that my contracts caused:** `chaos-shop/cli/uv.lock` was stale because the contracts package gained `pyyaml` dev dependencies (`ae54f2f`, in Tanzeel's folder, done at Usman's request).
- **Checks:**
  - Contract gate: ruff, `mypy --strict`, **259 tests**, schemas regenerate with no diff.
  - Locally, the non-Docker CI steps of chaos-shop (77 tests), the chaos CLI (37), and `ocp` (24) all pass, and every `uv.lock` passes `uv lock --check`.
- **Pushed `usman`.** CI does not run on it, because the workflows run only on PRs and on `main`.
- **CI on `tanzeel`** (`1a4f03a`): backend, migrations, mobile, contracts, and runner pass; **chaos-shop fails**. The log needs admin rights, and it is not one of the steps that can run here without Docker. It is Tanzeel's to check.

### Next
1. Send Tanzeel the message below.
2. When Tanzeel says go: the contracts go in through `contract/*` PRs and the rest through PRs, each merged once CI is green. This needs Usman to relax `CLAUDE.md` §11.1 (push only `usman`) to allow those PR branches; ask him first. Nothing is pushed to `main` directly.
3. Then: the `contracts-v0.1.0` tag, and Phase 1 for Stream B.
4. Still open: Docker Desktop, the Gemini key, and Supabase (Usman, by hand), then S0.7 (a), (c), and (d) (`next.md`).

### Waiting on
- **Tanzeel:**
  - his go-ahead for the merge;
  - fix the red chaos-shop CI job on `tanzeel`;
  - refresh `chaos-shop/cli/uv.lock` in the same PR as the contracts;
  - write the C7 deviations into `telemetry.md`;
  - add the C5 idempotency rule (issue 9) to the runner's refusal path.
- **Usman:** Docker Desktop, the Gemini key, and the Supabase projects; and the §11.1 decision when the merge starts.

### Message for Tanzeel

> Hi Tanzeel, done on `usman` (pushed, `ae54f2f`). I merged your branch first: no conflicts.
> 1. **C4, C6, C7:** C4 and C6 approved. C7 approved, provided you write your two accepted deviations into `telemetry.md` (no access line for `/metrics` and `/internal/*`; no `route` on cs-payments lines). Details in `docs/checks/contract-review-b.md`.
> 2. **Your 11 issues:** 1–7 and 9–11 are fixed, and 8 is accepted for a later PR (tell me which fixtures you need first). The per-issue replies with commits are in `contract-review-b.md`. Both must-fixes are in: the 4 KB cut is defined so a cut payload still validates (`cut_evidence_payload()`), and the recent-deploys record now carries `replicas` and `reason`. New fields: `unit`, `replicas`, `reason`, `lock_reason`; your DTOs ignore unknown keys, so your fixture test still passes. For issue 9, your runner's refusal path needs the new rule: claim `ocp:runner:idem:{execution_id}` before sending a refused or aborted result.
> 3. **CI:**
>    - Contracts are green locally: 259 tests. CI hasn't run on `usman`, because it only runs on PRs.
>    - **Your chaos-shop job is red on `tanzeel` (`1a4f03a`).** The non-Docker steps pass on my machine, so it's probably a Docker step (compose config, nginx `-t`, or the image build). Can you check the log? I can't open it without admin rights.
>    - Because my contracts package added `pyyaml` dev dependencies, `chaos-shop/cli/uv.lock` must be regenerated (`cd chaos-shop/cli && uv lock`, a two-line change) in the same PR that brings the contracts to `main`; otherwise `uv sync --locked` fails. I committed that refresh on `usman` (`ae54f2f`).
> 4. **Merge:** I won't touch `main` until you say so. Contracts through `contract/*` PRs, the rest through PRs.
>
> Also answered in `contract-review-b.md`: `chaos_net` declared plainly; ledger volume read-only at `/ledger`; `restart_count` over `OOMKilled`; scenario 5 by p95; `tools/ocp` as the single CLI (I'll add `seed-incident` there); and your runbook split is fine (format in §3).

## 2026-10-09 — Session 1: branch setup, contracts C1/C2/C3/C5/C8/C9, socket-proxy-ro

### Done
- **Set up this folder** as a git checkout of the new branch `usman`, started from `origin/tanzeel` at `6cac236`, so Tanzeel's 16 GB edits are in. The five loose copies of the docs were older (they still said 20 GB) and byte-identical to commit `91dddda8`, so nothing was lost; they are also backed up in the session scratchpad. Added a local pre-push hook that allows pushing only `usman`, and checked that it refuses `main`, `tanzeel`, and tags.
- **Commits on `usman`** (all pushed to `origin/usman`):

  | Commit | What |
  |---|---|
  | `524bdcb` | `CLAUDE.md` §11 (Usman's working rules) and the three session files |
  | `e99693b` | C1 domain models and enums (17 schemas) |
  | `2efbbe2` | C5 runner messages, HMAC signing, 7 test vectors |
  | `674dbfd` | C8 approval protocol (`approval-protocol.md`) |
  | `d257212` | C9 push payload (`push.md`) |
  | `adb9388` | C3 WebSocket protocol (`ws-protocol.md`) and 14 WS fixtures |
  | `c58495a` | C2 REST API (`openapi.yaml`) |
  | `36db805` | The 3 Phase 0 timeline fixtures |
  | `42392f8` | socket-proxy-ro in `infrastructure/compose/observability.yml`, `include:` line, `.env.example` |
  | `8de9823` | Stream B progress table in `docs/checks/week-1.md` |

- **Checks run:**
  - The contract gate (the same steps as `contracts.yml`) is **green**: ruff, `ruff format --check`, `mypy --strict`, **246 tests** (36 of Tanzeel's plus 210 new), schemas regenerate with no diff, and `uv sync --locked` passes.
  - `openapi.yaml` passes `openapi-spec-validator`.
  - The compose files were parsed and the proxy's safety settings checked by script. They were **not** run with Docker, because Docker is not installed here.
- **Installed** `uv` 0.12.7 with pip; `uv` downloaded Python 3.12.14 for the contracts environment.
- **Read** Tanzeel's C4, C6, and C7 as their reader. Nothing is blocking; the notes are below.
- **Explained Docker and Compose** in `learn.md` (section 3, "Docker, in more detail"), as Usman asked.
- **Wrote `next.md`**, the step-by-step plan for the next Claude session, as Usman asked. While writing it I found 4 new commits on `tanzeel` (A1.2: `cs-lb` and all 12 slot containers). They are not merged yet; a trial merge with `git merge-tree` was clean. They also bring two decisions for Usman: Tanzeel's "C7 deviations".

### Next
**`next.md` has the detailed, step-by-step version of this list for the next Claude session.**
1. **Send Tanzeel the message below** and agree a time for the 30-minute fixture walkthrough.
2. **Usman, by hand** (steps below): install Docker Desktop (S0.6), get a Gemini API key (B0.3), and create the two Supabase projects (B0.1).
3. **Next session, once 2 is done:**
   - S0.7 (a): start socket-proxy-ro with debug logging and a temporary Alloy (`discovery.docker` + `loki.source.docker`). Record the endpoints Alloy calls and a re-test with `NETWORKS=0`. Check that POST returns 403, that `/_ping` and `/version` return 200, and that `/libpod/*` returns 403. Write the results in `docs/checks/proxy.md`, section "(a) socket-proxy-ro and Alloy — Usman". Also run `docker compose --profile obs config`.
   - S0.7 (c): check that `gemini-embedding-001` with `output_dimensionality=768` returns 768 numbers that need L2 normalisation.
   - S0.7 (d): sign in to `ocp-dev`, then verify the token through the live JWKS (also SEC-010 in `week-1.md`).
   - If (a) shows `NETWORKS=1` is not needed, removing it changes architecture §2.11 and SEC-014, so ask Usman first.
4. **After the walkthrough:** apply the agreed contract changes. When Usman says so (`CLAUDE.md` §11.1), move the contract commits into `contract/*` PRs, merge, and tag `contracts-v0.1.0`. Then Phase 1 starts with B1.1 (migrations).
5. **Optional:** in the GitHub web UI, open a draft PR `usman → main` titled "DRAFT, do not merge", like Tanzeel's PR #1. CI workflows run only on PRs, so this is how the checks run on GitHub. This laptop has no `gh` CLI.

### Waiting on
- **Usman:**
  - Decide on Tanzeel's two C7 deviations (`next.md` §2): amend `contracts/telemetry.md` to match his code (recommended), or ask him to change the code.
  - Docker Desktop, a Gemini key, and the Supabase projects (steps below).
  - Whether to open the optional draft PR.
  - Your GitHub username, if you want the Usman lines in `.github/CODEOWNERS` switched on. Tanzeel left them commented out with a TODO.
- **Tanzeel:**
  - Read C1, C2, C3, C5, C8, and C9 (he is their reader), and answer the walkthrough questions in the message below.
  - The read-only proxy half of TEST-015 ("any POST through socket-proxy-ro → 403") goes in his `runner/tests/security/test_proxy.py` once socket-proxy-ro runs. Alternatively, Usman adds it under `tests/security/`; agree which.

### Message for Tanzeel

> Hi Tanzeel, my contracts are on my branch **`usman`**. I branched from `tanzeel` first, so your 16 GB edits are in.
> - **C1** `domain.py` + `enums.py`, **C2** `openapi.yaml` + `api.py`, **C3** `ws.py` + `ws-protocol.md` + `fixtures/ws/`, **C5** `runner.py` + `signing.py` + `fixtures/signing/vectors.json`, **C8** `approval.py` + `approval-protocol.md`, **C9** `push.py` + `push.md`. Every model is registered in `SCHEMAS` (40 new schemas). Contract tests: 246 green. Each contract is its own commit touching only `contracts/`, so each can become its own `contract/*` PR.
> - The **3 Phase 0 timelines** are in `contracts/fixtures/timelines/`, ready for the **30-minute walkthrough**. When suits you?
> - **socket-proxy-ro** is in `infrastructure/compose/observability.yml` with `LIBPOD_PING=0` and `LIBPOD_VERSION=0`, included from `compose.yaml`. My S0.7 results (ro proxy + Alloy, embeddings, JWKS) aren't in `proxy.md` yet: Docker isn't installed on my laptop yet.
>
> **Questions for the walkthrough:**
> 1. C5: I added `captured` to `execution_result`, because your C4 `rollback_step` reads `captured.previous_replicas` / `captured.previous_release`. OK?
> 2. C5: for a `failed` result (a step or the structural check failed) I left `error_code` null. Do you want codes for it?
> 3. C1/C5: the dry-run fields are my proposal, and you produce them: `would_change {target, change}`, `current {active_release, running_replicas, containers, cache_keys}`, `preconditions {rate_limit_ok, cooldown_ok, slots_available}`. The step statuses are `running`, `succeeded`, `failed`. Change anything you like.
> 4. C5: stream entries carry the message as JSON in one field named `msg`. The signature covers the JSON object as sent; keys are hex in the env vars.
> 5. Timelines: a step with `await_approval: true` marks where mock mode waits for the user's Approve. Does that fit `MockIncidentRepository`?
> 6. After signal verification fails, I left the original proposal `succeeded`; the failure shows in `health_after.verdict = fail` and the incident is `action_failed` (`scale_failed_rollback`). OK for the Execution Status screen?
> 7. Schema files are snake_case like your `deploy_record.json`, so API-005's `IncidentDetail.json` is `incident_detail.json`.
>
> **Reader notes on your C4, C6, C7:** all good, nothing blocking. On my side I'll set `honor_labels: true` on the Prometheus jobs (C7 §2.2), and `query_logs` with `level=ERROR` will also match `CRITICAL`, since a startup crash logs at CRITICAL.

### Usman's manual steps

**S0.6 — Docker Desktop** (needed for S0.7 (a) and for all of Stream B's local stack):
1. Windows 10 Home needs WSL 2. In PowerShell **as Administrator**, run `wsl --install`, then restart.
2. Install Docker Desktop for Windows from docker.com and choose the WSL 2 backend. Restart if asked.
3. Check in a terminal: `docker version` (note the *Server API version*) and `docker compose version` (must be 2.20 or newer, for `include:`).
4. Copy `.env.example` to a root `.env` (git-ignored) and replace the four testbed values with your own random hex, made with `python -c "import secrets; print(secrets.token_hex(24))"`. Compose reads every included file, so it asks for these even when you start only `socket-proxy-ro`.
5. If drive C: is short of space, move the disk image: Docker Desktop → Settings → Resources → Advanced.
6. Tell Claude "Docker is installed", and S0.7 (a) can run.

**B0.3 — Gemini API key** (one key serves the LLM and embeddings, ADR-13/14):
1. Create an API key in Google AI Studio.
2. In the repository root, create a file named `.env.local`. It is git-ignored by the `.env.*` rule. Add the line `GEMINI_API_KEY=<your key>`.
3. Never paste the key into the chat, a commit, or a prompt.

**B0.1 — Supabase** (`ocp-dev` and `ocp-bench`, both the same way):
1. Create both projects on supabase.com.
2. In Authentication settings, **turn off new-user sign-ups** (SEC-016).
3. In the project's JWT signing-key settings, **switch to an asymmetric signing key** (ECC or RSA). Without it the JWKS has no public key and backend auth cannot work (architecture §7.11).
4. In Authentication → Users, add users by hand: Usman, Tanzeel, and one **test user** for `ocp smoke` and `bench`.
5. Add these lines to `.env.local` (values from the `ocp-dev` project settings):
   - `SUPABASE_URL=…`
   - `SUPABASE_ANON_KEY=…` (the public anon key)
   - `OCP_TEST_EMAIL=…`
   - `OCP_TEST_PASSWORD=…`

### Notes for Usman's later tasks (found today)
- **B1.5:** the Prometheus jobs for `api-upstream` and `worker-upstream` need `honor_labels: true` (C7 §2.2).
- **B1.8:** the `query_logs` level filter `ERROR` should match `ERROR|CRITICAL`, because a startup crash (scenario 6) logs at `CRITICAL`. Non-JSON lines are labelled `UNKNOWN` and are reachable only with `ANY`.
- **B2.10:** RUN-015 says invalid runner results are dropped "with an audit entry", but §7.9 has no event name for that. Add one through a contract change when building it.
