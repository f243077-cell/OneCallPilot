# Next session — start here

> **For:** the next Claude Code session working for **Usman** in this checkout.
> **Written:** 2026-10-09, at the end of session 1.
> **Read order:** `CLAUDE.md` (its §11 holds Usman's rules), `context.md`, `handoff.md`, then this file.
> **When the session ends,** rewrite this file with the steps for the session after it.

---

## 0. Rules for this session (short form of `CLAUDE.md` §11)

- Commit only to `usman`, and push only with `git push origin usman`. A local pre-push hook refuses every other target; never bypass it with `--no-verify`. Nothing goes to `tanzeel` or `main`.
- Bring in Tanzeel's work with `git fetch origin` and `git merge origin/tanzeel`. Never rebase `usman`.
- A contract change is a commit that touches only `contracts/` (plus regenerated schemas and fixtures).
- Commit messages:
  - match the Conventional Commits style in `git log`;
  - write multi-line messages with a bash heredoc;
  - **no attribution lines of any kind.**
- Before every push, check that `git ls-files` lists no `.env*` file except `.env.example`, no `google-services.json`, and nothing under `infrastructure/secrets/` except `.gitkeep`.
- Never print, log, commit, or paste a key, token, or password. Scripts read them from `.env.local` themselves.
- Spike scripts and configs are throwaway: keep them in the scratchpad and commit only the results, as Tanzeel did for the nginx spike.

## 1. Start of the session (always)

1. Check `git status`, which should be clean, then run `git fetch origin`.
2. **Merge Tanzeel's new work:** `git log --oneline usman..origin/tanzeel`.
   - On 2026-10-09 there were 4 new commits there: task A1.2, which adds `cs-lb`, all 12 slot containers, releases 1.5.0 and 2.2.0, CI, and notes in `docs/checks/week-1.md`.
   - A trial merge (`git merge-tree --write-tree usman origin/tanzeel`) was clean. The only file both branches had changed was `docs/checks/week-1.md`.
   - Run `git merge --no-edit origin/tanzeel`, then the contract gate in step 5, then `git push origin usman`.
   - If a conflict appears, it can only be in a shared file (`compose.yaml`, `.env.example`, `docs/checks/*.md`): keep both sides' lines.
   - Then read Tanzeel's new sections in `docs/checks/week-1.md`: "A1.2 — cs-lb and slots", "C7 deviations", and "chaos_net for other Compose files".
3. **Look for news from Tanzeel:**
   - comments on PR #1 (`curl -s https://api.github.com/repos/f243077-cell/OneCallPilot/issues/1/comments`), and any new PRs;
   - ask Usman whether the 30-minute fixture walkthrough has happened, and what Tanzeel said.
4. **Ask Usman which of these are ready.** Each one unlocks a step in §3.
   - Docker Desktop installed, with the root `.env` made from `.env.example` (`handoff.md`, step S0.6)?
   - `GEMINI_API_KEY` in `.env.local`?
   - Supabase `ocp-dev` set up, with `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `OCP_TEST_EMAIL`, and `OCP_TEST_PASSWORD` in `.env.local`?
   - Tanzeel's answers to the 7 walkthrough questions (`handoff.md`, "Message for Tanzeel")?
5. **Confirm the contract gate is green.** This is the same sequence as `.github/workflows/contracts.yml`. It gave 246 tests on 2026-10-09, and Tanzeel's A1.2 commits do not touch `contracts/`.

   ```bash
   cd contracts/python
   uv sync --locked && uv run ruff check . && uv run ruff format --check . \
     && uv run mypy oncallpilot_contracts tests && uv run pytest -q \
     && uv run python -m oncallpilot_contracts.schema_export && git diff --exit-code -- ../schemas
   ```

## 2. Decisions waiting for Usman (ask early, in one go)

1. **Tanzeel's two C7 deviations.** He recorded them in `week-1.md` on `tanzeel` and left the decision to Usman:
   - (a) cs-api, cs-payments, and cs-lb write no access-log line for `/metrics` and `/internal/*`;
   - (b) cs-payments request lines have no `route` field.

   `contracts/telemetry.md` was not changed, so the choice is to amend C7 to match or to ask Tanzeel to change his code. **Recommend amending C7.** Both deviations are sensible: admin calls stay out of the logs the agent reads, and C7's `route` values name cs-api routes only. No detector rule or agent tool depends on either. If Usman agrees, make one commit that edits only `contracts/telemetry.md` (§3.2 and §3.3), and tell Tanzeel.
2. **The answers to the 7 walkthrough questions:** apply them (§3.1).
3. **A draft PR `usman → main`,** opened by Usman in the GitHub web UI and marked do-not-merge, so CI runs on GitHub. CI runs only on PRs, and there is no `gh` CLI here.
4. **Whether to commit the fixture generators** (see §5).
5. *Optional:* Usman's GitHub username, to switch on his lines in `.github/CODEOWNERS`.

## 3. Work, in order: do each step whose prerequisite is ready

### 3.1 Apply the walkthrough feedback (needs: Tanzeel's answers)
- Change the models in `contracts/python/oncallpilot_contracts/`, regenerate the schemas, update the fixtures, and run the gate. Make one commit per contract, touching only `contracts/`. `contracts/VERSION` stays `0.1.0` until the `contracts-v0.1.0` tag.
- If a runner message changes, the signatures in `fixtures/signing/vectors.json` must be recomputed with `oncallpilot_contracts.signing.sign` and the test keys in that file.
- If a proposal changes, its `fingerprint` in the timelines must follow the formula in `approval-protocol.md` §2.
- The tests catch every mismatch.

### 3.2 S0.7 (a): socket-proxy-ro and Alloy (needs: Docker Desktop and the root `.env`)
**Goal:** fill the `docs/checks/proxy.md` section "(a) socket-proxy-ro and Alloy — Usman", in the same style as Tanzeel's rw section.

1. Run `docker version` and record the Server API version (Tanzeel's is 1.56). Run `docker compose version`, which must be 2.20 or newer.
2. Run `docker compose --profile obs config --quiet`. This is the first real validation of `observability.yml`.
3. Start the proxy with debug logging: `SOCKET_PROXY_RO_LOG_LEVEL=debug docker compose --profile obs up -d --wait socket-proxy-ro`.
4. Start a throwaway log source that carries the label Alloy filters on: `docker run -d --name spike-logger --label oncallpilot.logs=true busybox:1.37 sh -c 'while true; do echo spike-line; sleep 2; done'`.
5. Start a throwaway Alloy container on the network `oncallpilot_ro_proxy_net`. Pin a current `grafana/alloy` tag and record it. Keep the config below in the scratchpad, mounted into the container, and check its syntax against the docs of the pinned version:

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

   In Git Bash, set `MSYS_NO_PATHCONV=1` before any `docker run -v …`, as `runner/tests/security/run_proxy_test.sh` does.
6. Record whether Alloy prints the `spike-line` entries, which endpoints it called (`docker compose logs socket-proxy-ro`), and whether there was any 403.
7. Re-test with `NETWORKS=0`. Start a second throwaway proxy by hand with the same settings except `NETWORKS=0`, and point Alloy at it. If discovery still works, removing `NETWORKS=1` would change architecture §2.11 and SEC-014: **ask Usman first**, then tell Tanzeel.
8. From a throwaway curl container on `oncallpilot_ro_proxy_net`, check:

   | Request | Expected |
   |---|---|
   | `GET /containers/json` | 200 |
   | `GET /_ping` | 200 |
   | `GET /version` | 200 |
   | `POST /containers/create` | 403 |
   | `POST /containers/spike-logger/restart` | 403 |
   | `DELETE /containers/spike-logger` | 403 |
   | `GET /libpod/_ping` | 403 |
   | `GET /libpod/version` | 403 |

9. Remove the spike containers, and stop the proxy if this session started it.
10. Tell Tanzeel that the ro proxy runs, so the ro half of TEST-015 ("any POST → 403") can be added. It goes either in his `runner/tests/security/test_proxy.py` or, written by Usman, in `tests/security/`; agree which.

**Downloads are slow here (about 370 kB/s), and the Alloy image is large.** Pull images in the background with a long timeout.

### 3.3 S0.7 (c): `gemini-embedding-001` at 768 dimensions (needs: `GEMINI_API_KEY`)
- Write a throwaway script in the scratchpad and run it with `uv run --no-project --python 3.12 --with google-genai python <script>`. The script reads the key from `.env.local` and never prints it.
- Embed one text with `output_dimensionality=768`, once with `task_type=RETRIEVAL_DOCUMENT` and once with `RETRIEVAL_QUERY`. Check the call against the current SDK docs.
- Expect:
  - each vector has exactly 768 values;
  - its L2 norm is **not** 1, because the API normalises only the 3072-dimension output;
  - after dividing by the norm it is 1 ± 1e-6.
- Record the SDK version, the model, both norms, and the date in `proxy.md` section (c). If anything differs, ADR-14 and AI-027 may need a new ADR: ask Usman.

### 3.4 S0.7 (d): Supabase login and JWKS (needs: Supabase and the `.env.local` values)
Write a throwaway script and run it with `uv run --no-project --python 3.12 --with "pyjwt[crypto]" --with httpx python <script>`. It should:

1. Sign in: `POST {SUPABASE_URL}/auth/v1/token?grant_type=password` with the header `apikey: <SUPABASE_ANON_KEY>` and the test user's email and password. Keep the access token in memory only.
2. Fetch the keys: `GET {SUPABASE_URL}/auth/v1/.well-known/jwks.json`. There must be at least one key; note its `kid` and `alg`, which must be asymmetric (ES256 or RS256).
3. Verify the token with PyJWT's `PyJWKClient`: the signature, `aud = "authenticated"`, `iss = {SUPABASE_URL}/auth/v1`, and `exp`.
4. Try a sign-up: `POST {SUPABASE_URL}/auth/v1/signup` with a made-up address must fail (SEC-016).

Record the results in `proxy.md` section (d), and in `week-1.md`, where SEC-010 asks for them.

### 3.5 Record and push
Update the Stream B rows in `docs/checks/week-1.md` for whatever finished, commit with `docs(checks): …`, and push.

## 4. Do not start yet

- **Phase 1** (B1.1 migrations onwards) starts only after the contracts are merged into `main` and tagged `contracts-v0.1.0`, and that happens only when Usman says so (`CLAUDE.md` §11.1, `phases.md` §11).
- **If everything in §3 is blocked,** ask Usman whether to start drafting **C10**, the benchmark records contract:
  - `benchmark/scenarios.yaml` schema and `benchmark/schema/runs.csv.md` (BENCH-001, BENCH-007);
  - due at the end of week 2;
  - the one Stream B contract that needs nothing else.

## 5. Things to know

- **The fixture generators are gone.** Session 1 built `fixtures/signing/vectors.json`, `fixtures/ws/*.json`, and `fixtures/timelines/*.json` with throwaway scripts that were **not** committed; they lived in session 1's temporary scratchpad. To change a fixture, edit the JSON, recompute what depends on it (signatures, proposal fingerprints), and let the tests confirm. If Usman wants future changes to be easy, write a small committed generator first.
- **Tools:**
  - `uv` 0.12.7 was installed with pip into the system Python 3.13; if `uv` is not on PATH, use `python -m uv`. `uv` manages Python 3.12.14 for `contracts/python/.venv`.
  - There is no Docker until Usman installs it, and no `gh` CLI. The GitHub API works with `curl` because the repository is public. Pushing works through Git Credential Manager.
- **Notes for later Stream B tasks:**
  - **B1.5:**
    - The Prometheus jobs need `honor_labels: true` (C7 §2.2).
    - Prometheus and backend-worker join `chaos_net` by naming it plainly (`chaos_net: {}`) or not declaring it at all. **Not** with `external: true`, which fails on a fresh `up` (Tanzeel tested this; see `week-1.md`).
    - `cs-lb` is published only on `127.0.0.1:8080`.
  - **B1.8:** the `query_logs` level `ERROR` should match `ERROR|CRITICAL`, because a startup crash logs at `CRITICAL`. Non-JSON lines are labelled `UNKNOWN`.
  - **B2.10:** RUN-015's audit entry for a dropped runner result has no event name in architecture §7.9. Add one through a contract change.

## 6. The session is done when

- Tanzeel's work is merged and pushed, and the contract gate is green.
- The decisions in §2 have been asked, and the answers applied.
- Every possible part of S0.7 is recorded in `proxy.md` and `week-1.md`.
- `handoff.md` has a new dated entry; `context.md` and this file are rewritten; `learn.md` has a new entry.
- Everything is committed and pushed to `origin/usman`.
