# Handoff — session log

> One dated entry per session, **newest first**, each with four parts: **Done** (with commits), **Next** (in order), **Waiting on** (Usman or Tanzeel), and **Message for Tanzeel** (when there is one). Old entries are never edited (`CLAUDE.md` §11.4).

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
