# Next session — start here

> **For:** the next Claude Code session working for **Usman** in this checkout.
> **Written:** 2026-10-10, at the end of session 3.
> **Read order:** `CLAUDE.md` (§11 holds Usman's rules), `context.md`, `handoff.md`, then this file.
> **When the session ends,** rewrite this file with the steps for the session after it.

**Goal: close Phase 0.** What is still open is listed in `handoff.md`, "Phase 0 status".

## 0. Rules (short form of `CLAUDE.md` §11)
- Push `usman` with `git push origin usman`. Usman's PR branches (`contract/*`, `feat/b-*`, `fix/*`, `chore/*`, `docs/*`, `test/*`, `bench/*`) may also be pushed, but **only when Usman asks for that PR**.
- **Never** push or merge directly into `main` or `tanzeel`; never push `feat/a-*` or tags. Work reaches `main` only through a PR with green CI. The pre-push hook enforces this; never bypass it with `--no-verify`.
- Merge `origin/tanzeel` into `usman`; never rebase `usman`. A contract change is a commit that touches only `contracts/`.
- No attribution lines; write multi-line commit messages with a bash heredoc; run the secrets check before each push; never print a key or token.

## 1. Start of the session
1. `git status` should be clean. Then `git fetch origin` and `git log --oneline usman..origin/tanzeel`. If there is anything new, run `git merge-tree --write-tree --name-only usman origin/tanzeel`; if that is clean, merge.
2. Run the contract gate (259 tests) and `uv lock --check` in `contracts/python`, `chaos-shop`, `chaos-shop/cli`, `runner`, and `tools/ocp`.
3. Check CI on Tanzeel's newest commit: `curl -s https://api.github.com/repos/f243077-cell/OneCallPilot/commits/<sha>/check-runs`. The chaos-shop job was red on `1a4f03a` and `b71ed6b`.
4. **Docker:** on 2026-10-10 Docker Desktop was installed but its engine was not running (WSL 2 missing). Docker's `bin` folder may not be on PATH in Git Bash; if so, use `export PATH="/c/Users/Usman Ghani/AppData/Local/Programs/DockerDesktop/resources/bin:$PATH"`. Check that `docker version` shows a *Server*. If it does not, give Usman the steps in `handoff.md` (session 3) and do §3 instead.
5. Ask Usman: is the Gemini key in `.env.local`? Is Supabase set up? Has Tanzeel said "go"?

## 2. S0.7, Usman's three checks (record each in `docs/checks/proxy.md`)
**(a) socket-proxy-ro and Alloy** (needs Docker and the root `.env`). Write the results under "(a) socket-proxy-ro and Alloy — Usman", in the style of Tanzeel's rw section.
1. `docker compose --profile obs config --quiet`, then `SOCKET_PROXY_RO_LOG_LEVEL=debug docker compose --profile obs up -d --wait socket-proxy-ro`.
2. A throwaway log source: `MSYS_NO_PATHCONV=1 docker run -d --name spike-logger --label oncallpilot.logs=true busybox:1.37 sh -c 'while true; do echo spike-line; sleep 2; done'`.
3. A throwaway Alloy (pin a current `grafana/alloy` tag) on `oncallpilot_ro_proxy_net`, with the scratchpad config below. Record whether it prints `spike-line`, and which endpoints it called (`docker compose logs socket-proxy-ro`).

   ```alloy
   discovery.docker "spike" {
     host = "tcp://socket-proxy-ro:2375"
     filter {
       name   = "label"
       values = ["oncallpilot.logs=true"]
     }
   }
   loki.source.docker "spike" {
     host       = "tcp://socket-proxy-ro:2375"
     targets    = discovery.docker.spike.targets
     forward_to = [loki.echo.out.receiver]
   }
   loki.echo "out" { }
   ```

4. Re-test with a second throwaway proxy that has `NETWORKS=0`. If Alloy still works, removing `NETWORKS=1` changes architecture §2.11 and SEC-014: **ask Usman first**, then tell Tanzeel.
5. From a curl container on `oncallpilot_ro_proxy_net`, expect:
   - 200 for `GET /containers/json`, `/_ping`, and `/version`;
   - 403 for `POST /containers/create`, `POST /containers/spike-logger/restart`, `DELETE /containers/spike-logger`, `GET /libpod/_ping`, and `GET /libpod/version`.
6. Clean up the spike containers. Record the Docker API version from `docker version`.
7. While Docker is up: run the chaos-shop CI's Docker steps locally to help Tanzeel find his red job:
   - `docker compose --env-file .env.example --profile testbed --profile testbed-slots config --quiet`;
   - the nginx `-t` check;
   - the image build.

**Downloads are slow on this laptop** (about 370 kB/s); pull images in the background with long timeouts.

**(c) `gemini-embedding-001` at 768 dimensions** (needs `GEMINI_API_KEY` in `.env.local`).
- Write a throwaway script and run it with `uv run --no-project --python 3.12 --with google-genai`. It reads the key itself and never prints it.
- Embed once with `RETRIEVAL_DOCUMENT` and once with `RETRIEVAL_QUERY`.
- Expect 768 values, a norm that is not 1, and a norm of 1 ± 1e-6 after normalising. Record the SDK version and both norms.

**(d) Supabase login and JWKS** (needs Supabase and `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `OCP_TEST_EMAIL`, `OCP_TEST_PASSWORD` in `.env.local`).
- Write a throwaway script and run it with `uv run --no-project --python 3.12 --with "pyjwt[crypto]" --with httpx`.
- Sign in with the password grant, fetch `/auth/v1/.well-known/jwks.json` (the key must be asymmetric), then verify the token with `PyJWKClient`: `aud = authenticated`, `iss = {SUPABASE_URL}/auth/v1`, and `exp`.
- `POST /auth/v1/signup` must fail (SEC-016).
- Record the results in `proxy.md` (d) and `week-1.md` (SEC-010).

Then update the Stream B rows in `docs/checks/week-1.md`, commit (`docs(checks): …`), and push `usman`.

## 3. Phase 0 into `main` (only after Tanzeel says go and his chaos-shop CI is green)
1. Agree the PR plan with Tanzeel and Usman so that `main` never sees a conflict:
   - the contracts first: one `contract/*` PR for v0.1, or one per contract;
   - include the `chaos-shop/cli/uv.lock` refresh with the contracts;
   - then the rest;
   - then the tag `contracts-v0.1.0`, which needs Usman's say-so, because tags are blocked by the hook.
2. **Watch out:** if contracts are squash-merged and full branches are merged later, git can report add/add conflicts on files that changed again after the squash. Pick one route. The simplest is to merge `usman` (which already contains all of `tanzeel`) through one PR, if Tanzeel agrees; otherwise rebase each later branch on `main`.
3. Push a PR branch only when Usman asks. There is no `gh` CLI, so Usman may open the PR in the GitHub web UI. Merge only when every CI job is green.

## 4. Other open items
- Tanzeel's issue 8 fixtures (MOB-019, MOB-011, MOB-012, MOB-017), once he says which comes first. The session 1 fixture generators lived only in a scratchpad and may be gone; ask Usman whether to commit one.
- If all is blocked: ask Usman whether to start C10 (benchmark records).

## 5. Notes for later Stream B tasks
- **B1.5:** `honor_labels: true`; name `chaos_net` plainly (`chaos_net: {}`), never `external: true`; loggers are `shop.*`.
- **B1.7:** scenario 5 is detected by `p95_latency`.
- **B1.8:** `ERROR` should match `ERROR|CRITICAL`.
- **B2.2:** `restart_count` instead of `OOMKilled`; ledger volume `deploy_ledger`, read-only at `/ledger`, `LEDGER_PATH=/ledger/deploys.jsonl`, files owned by uid 10001.
- **B1.4:** `seed-incident` goes into `tools/ocp`.
- **B2.10:** name the audit event for a dropped runner result.

## 6. The session is done when
Every Phase 0 item that its prerequisites allow is closed and recorded; the session files are updated (a new dated `handoff.md` entry; `context.md` and this file rewritten; `learn.md` appended); and everything is pushed to `usman`.
