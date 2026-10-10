# Week 3 — checks and progress (Phase 2)

> Evidence for Phase 2 (`phases.md` Phase 2, Weeks 3–4). Stream A (Tanzeel) on branch `tanzeel`.

## Phase 2 progress — Stream A (Tanzeel)

| Task | Status | Date | Evidence |
|---|---|---|---|
| A2.1 Runner: consumer group, signature and expiry verification (shared vector), catalogue and target allowlist, free-form rejection | done (on `tanzeel`) | 2026-10-10 | Below |
| A2.2 socket-proxy-rw config; proxy security tests (RUN-017, TEST-015) | done (on `tanzeel`); one known limitation recorded | 2026-10-10 | Below |

### A2.1 — runner verification (RUN-001…RUN-004, RUN-018; RUN-005's SET NX brought forward)

**Read first** (2026-10-10): architecture §2.7, §2.10, §2.11, §5.2, §5.10, §5.12, §7.1–§7.6 and §9.5; CLAUDE.md §6 and §8; C4 (`contracts/actions.yaml`, identical on both branches); and C5 (`runner.py`, `signing.py`) plus the signing vector from `origin/usman` at `0582f37`, through a temporary worktree outside the repository. Nothing was copied into `tanzeel`.
- **The vector is correct:** both keys decode to 32 bytes of test-only ASCII, every `canonical` string matches its message, and each signature verifies or fails exactly as labelled (checked independently with stdlib HMAC).
- **Three open points in C5,** recorded as issues 9–11 in `docs/checks/contract-review-a.md`:
  - a `BAD_SIGNATURE` refusal echoes unverified ids;
  - a message too malformed to answer has no C5 result;
  - a differing `catalogue_version` has no rule.
- **Rulings (2026-10-10):**
  - The runner implements the C5 checks itself, because C5 is not on this branch.
  - It follows C5 on refusals and flags the race.
  - SET NX idempotency is brought forward from A2.5.
  - The packages are approved.

**What exists now** (`runner/`):
- **`oncallpilot_runner/signing.py`:** canonical JSON (sorted keys, no whitespace, UTF-8), HMAC-SHA256 with a constant-time compare, and hex keys of at least 32 bytes. `execute` verifies with ACTION_SIGNING_KEY only; `dry_run` and every result use RUNNER_LINK_KEY. `repr` hides the keys.
- **`checks.py`:** C5's checks, in C5's order:
  1. signature → `BAD_SIGNATURE`;
  2. shape → `VALIDATION_ERROR` (every field present and no other; approval fields required on `execute`, SEC-002, and refused on `dry_run`; UTC timestamps; `expires_at` after `issued_at`);
  3. expiry → `REQUEST_EXPIRED`;
  4. catalogue → `ACTION_NOT_ALLOWED` (the action must be enabled and have a registered handler);
  5. parameters → `VALIDATION_ERROR` (exactly the catalogue's parameters, enum values or bounded integers; a boolean is not an integer; `ledger_releases` read from the deploy ledger, and an unreadable ledger is refused);
  6. targets → `TARGET_NOT_ALLOWED`;
  7. `SET NX ocp:runner:idem:{execution_id}` with a 24 h TTL: a replay is acknowledged and ignored, with no result.
- **Refusals:** every refusal is a signed C5 `refused` result (`dry_run_result` or `execution_result`). The worker turns it into `runner_refused`.
- **Dropped messages:** a message too malformed to answer is logged and acknowledged with no result. That covers fields other than exactly `msg`, invalid JSON, no usable `type` or `request_id`, or an `execute` without a UUID `execution_id`.
- **Unexpected errors:** an unexpected error in a check still produces a refusal.
- **`consumer.py`:** consumer group `runner` on `ocp:runner:requests`, created from the start of the stream if it is missing.
  - At start-up it re-reads its own pending entries (consumer name = hostname). While running it takes over entries idle for more than 300 s with `XAUTOCLAIM` (the v1.4 rule).
  - The result is written before the `XACK`, so a crash repeats a result and never loses one.
  - A Redis error waits with backoff (1, 2, 4 … 30 s) without acknowledging.
  - A heartbeat file after every poll drives the container health check.
- **`streams.py`:** the Redis commands used, all within the `runner` ACL user's rules (§2.7).
- **`catalogue.py`, `targets.py`, `handlers.py`:**
  - The catalogue is the shared `contracts/actions.yaml`.
  - `runner/targets.yaml` holds the per-service labels, `^cs-(api|worker|redis)(-[0-9]+)*$`, and the slot releases. `container_allowed()` is the check every Docker call must pass from A2.3 on.
  - The handler set is the four enabled actions. A test fails if it differs from the catalogue's enabled set (RUN-003), and the runner refuses to start if they differ.
- **No Docker calls and no handler bodies yet** (A2.3, A2.4): an accepted request is logged and changes nothing.
- **Start-up:** the runner refuses to start without both keys, with identical keys, or without the catalogue, the allowlist or `RUNNER_REDIS_URL`. It never logs a key.
- **`runner/Dockerfile`:**
  - `python:3.12.15-slim`, uid 10001 (the ledger's owner).
  - The catalogue comes from the `contracts` build context: `docker build --build-context contracts=contracts runner`.
  - Image 196 MB.
- **`tests/publish_signed.py`:** the fake publisher. It builds C5 requests, signs them with the shared vector's TEST-ONLY keys (`--vector`, or `OCP_CONTRACTS_DIR`), and supports `--wrong-key`, `--tamper`, `--expired` and `--repeat`. The tests use its functions.
- **Packages** (approved): redis 8.1.0, pyyaml 6.0.3, and types-PyYAML (dev).
- **`.env.example`** lists `ACTION_SIGNING_KEY`, `RUNNER_LINK_KEY`, `RUNNER_REDIS_URL` and `DOCKER_API_VERSION` with dummy values.
- **CI** (`runner.yml`): locked sync, ruff, `mypy --strict`, pytest, and the image build.

**Checks (2026-10-10):**
- ruff and `mypy --strict` (package and tests) are clean.
- **Unit tests on `tanzeel`: 78 passed, 3 skipped** (the vector tests; the vector is not on this branch). With `OCP_CONTRACTS_DIR` pointing at `origin/usman`: **81 passed**. The keys are the vector's test keys when it is present, random 32-byte keys otherwise. Every listed case fails closed:
  - **Valid:** each enabled action is accepted as `dry_run` and `execute`; the vector's two valid requests are accepted.
  - **Forged signature:** a zero `sig`, tampered params, a missing `sig` → `BAD_SIGNATURE`. The signature is checked before anything else (a forged, expired, disabled, malformed message still reads `BAD_SIGNATURE`).
  - **Wrong key:** an execute signed with RUNNER_LINK_KEY, a dry run signed with ACTION_SIGNING_KEY, an unknown key → `BAD_SIGNATURE`. The two invalid vector requests → `BAD_SIGNATURE`.
  - **Expired:** aged 61 s, and exactly at `expires_at` → `REQUEST_EXPIRED` (RUN-002).
  - **Replay:** the same signed execute twice → accepted once, then a duplicate with no result; replayed after expiry → `REQUEST_EXPIRED`.
  - **Unknown or disabled action:** `exec_shell`, `delete_container`, `run_migration_rollback`, and an enabled action without a handler → `ACTION_NOT_ALLOWED` (RUN-003).
  - **Target:** a service missing from `targets.yaml` → `TARGET_NOT_ALLOWED`.
    - `cs-api-140-1` without the label, `backend-api`, `socket-proxy-rw`, and a wrong service → not allowed (RUN-004, pure check).
  - **Free-form:** 15 cases → `VALIDATION_ERROR` (RUN-018).
    - Strings: `service="api; rm -rf /"`, `backend-api`, `payments`.
    - Extra or missing parameters.
    - Integers: `replicas` as `"3"`, `0`, `6` or `true`.
    - Releases: the active release, one without slots, `../../etc`, one not in the history.
    - `cache="*"`.
    - An unreadable ledger.
  - **Malformed:** 7 unanswerable entries are dropped. A signed `execute` with a bad shape gets `VALIDATION_ERROR` (14 cases: each approval field null, bad fingerprint, version, timestamps, params). So do extra fields, a dry run with approval fields, and `expires_at` before `issued_at`.
  - **Every refusal** is checked to verify with RUNNER_LINK_KEY and not with ACTION_SIGNING_KEY, and to echo the request's ids as C5 requires.
  - **Consumer:** a refusal is published then acknowledged; accepted, duplicate and dropped publish nothing; own pending entries are re-read after a restart; an entry idle for 299,999 ms is not taken over and one idle for 300,000 ms is; a crash between check and `XACK` repeats nothing (duplicate); a bug in a check still refuses; Redis outages retry with 1, 2, 4 s backoff.
- **Live** (`uv run pytest -m live`, the real image against a throwaway `redis:7.4.11-alpine` with the §2.7 `runner` ACL user, on a private network): **3 passed**.
  - 9 refusals with the right codes, all signed with the link key only. The valid request was accepted once, its replay was a duplicate, the bad JSON was dropped, and the logs contain no key.
  - As the `runner` user, `XADD ocp:events`, `GET ocp:challenge:*`, `KEYS *`, `CONFIG GET *`, `FLUSHALL` and `EVAL` fail with NOPERM; `PING` and `SET NX` on `ocp:runner:*` work (SEC-004, the parts the runner needs).
  - The container runs read-only, `cap_drop: ALL`, `no-new-privileges`, user `runner` (10001), 128 MiB limit, no Docker socket mount and no published port. **It uses 25 MiB.**

**Memory:** the runner uses 25 MiB of its 128 MiB limit. It runs only in the live test; the stack's limits are unchanged.

**Deviations:**
1. **No runner service in compose yet.** copilot-redis (Usman's `copilot.yml`, B1.3) does not exist, and `ocp up` starts the `runner` profile. A runner without Redis would never become healthy and would break `ocp up`. The service is added when copilot-redis exists; until then the image is tested by the live test.
2. **C5 is implemented in the runner** instead of imported, because `oncallpilot_contracts.runner` and `.signing` are only on `origin/usman`. It is cross-checked against the shared vector, and can switch to the contracts package after the merge.
3. **RUN-005's `SET NX` is in A2.1** (ruling 2026-10-10). Rate limit, cooldown and the state fingerprint stay in A2.5 and A2.3.
4. **Coverage not measured** at the time; measured after A2.1 with pytest-cov (approved 2026-10-10, dev only): **91.6 %** of `oncallpilot_runner` from the unit tests alone (`streams.py` is 50 %: its real Redis calls run only in the live test). CI fails below 85 %.

**Open, for Usman** (C5 issues 9–11 in `contract-review-a.md`): the forged-refusal race, results for unanswerable messages, and the `catalogue_version` rule. The new `.env.example` entries also name the services that need each key (backend-api and backend-worker on his side).

### A2.2 — socket-proxy-rw configuration and security tests (RUN-017, TEST-015)

**Already covered before A2.2** (S0.7, `docs/checks/proxy.md`, 12/12): container list and inspect, start, stop, restart allowed; create, exec, delete, image pull and list, network create, volume create, container update, version, ping and events refused.

**Configuration:** `infrastructure/compose/runner.yml` matches architecture §2.11 exactly, so it is unchanged: `CONTAINERS=1, ALLOW_START=1, ALLOW_STOP=1, ALLOW_RESTARTS=1, POST=0`, with the image's default-on `EVENTS`, `PING`, `VERSION`, `LIBPOD_PING`, `LIBPOD_VERSION` set to `0`; socket mounted read-only; read-only root, `/run` tmpfs; only on the internal `rw_proxy_net`; no published port.

**How the proxy decides** (read from the image's `/templates/haproxy.cfg`, 3.4.6-r0-ls101): explicit allows for `/containers/{id}/(stop|restart|kill)` (`ALLOW_RESTARTS`), `/start`, `/stop`; then `deny unless GET` (POST=0); then `GET /containers*` (`CONTAINERS`); `logs`, `archive`, `export`, `top`, `changes` are denied first unless their own `ALLOW_*` is set; everything else is denied.

**Tests added** (`runner/tests/security/test_proxy.py`, now 54 tests, all passed on 2026-10-10 against the real proxy):
- **Allowed:** list, inspect, stats; stop, start, restart; **kill** (allowed by §2.10, and grouped with `ALLOW_RESTARTS` in the image).
- **Refused with 403:**
  - create, including `Privileged=true` with a `/` bind;
  - exec create and exec start; delete;
  - every other container POST: pause, unpause, rename, wait, resize, attach, update;
  - prune, build, commit, session, auth;
  - image pull and list; network and volume calls;
  - `info`, `system/df`, `secrets`, `services`, `nodes`, `swarm`, `plugins`, `tasks`, `configs`, `distribution`;
  - `logs`, `export`, `top`, `changes`, and `archive` for GET, HEAD and PUT;
  - libpod;
  - path tricks (double slash, `%61rchive`, encoded `?`) do not bypass the deny.
- **The socket is unreachable except through the proxy:** the test container (same networking as the runner: `rw_proxy_net`) has no `/var/run/docker.sock` or `/run/docker.sock`, and a default Docker client cannot reach a socket. The runner image itself was already shown in A2.1's live test to run with no socket mount, no port, read-only root and no capabilities.
- **CI:** new job `runner-proxy` in `runner.yml` runs `run_proxy_test.sh` on every push and PR (GitHub-hosted runners have Docker). The script now derives the API version from the host's `docker version` when `DOCKER_API_VERSION` is unset; tested locally both ways (54/54 each).

**Gaps between the docs and the proxy:**
1. **`GET /containers/{id}/attach/ws` returns 101** (WebSocket stdio attach). It is a GET under `/containers`, which §2.10 allows, and the proxy cannot block it without also blocking inspect. No command can execute (the containers' PID 1 do not read commands from stdin), but it goes beyond "start, stop, restart". Ruled 2026-10-10: **documented as a known limitation**, with a test that records the behaviour, and a proposed line for architecture §7.12 in `proxy.md`.
2. **`kill` is allowed:** §2.10 lists it, RUN-017 and §2.11 do not. The image grants it with `ALLOW_RESTARTS`; the runner does not use it.
3. **The start/stop/restart/kill allow rules ignore the HTTP method,** so e.g. `DELETE /containers/x/stop` reaches Docker. Docker answers 404 (it reads it as a container named `x/stop`), so nothing happens; recorded, no test needed beyond the delete-is-403 test.
4. **`GET /containers/{id}/json` returns every container's environment.** A compromised runner can read other containers' env vars through inspect, which §2.11's limitation note does not mention. Endpoint-level filtering cannot avoid it; keeping secrets in mounted files rather than env (as the FCM key already is) would. For Usman: an architecture-level question, not a runner change.

**Not covered:** the ro-proxy half of TEST-015 (any POST through `socket-proxy-ro` → 403) waits for Usman's `observability.yml`.

**Gate:** TEST-015 is Gate G2 criterion 3: `docs/checks/gate-g2.md` (created) records it as green for socket-proxy-rw.

## Phase 0 close-out, part 2 (2026-10-10): one PR into `main`

**Ruling:** merging `origin/usman` into `tanzeel` is allowed; Phase 0 from both streams goes into `main` through one PR from `tanzeel`.

### 1. CI was red: chaos-shop, step "Images build (every release)"

- **Red from `1a4f03a`, green at `3539d80`.** The step failed in 1 s (12 s when green).
- **Cause:** A2.1 added `DOCKER_API_VERSION=1.56` to `.env.example`. GitHub's `ubuntu-24.04` image has **Docker 28.0.4 (API 1.48), Compose v2.38.2, buildx 0.37.2**. That Compose passes `--env-file` values into the `buildx bake` subprocess, so buildx sent API 1.56 to a 1.48 daemon: `Error response from daemon: client version 1.56 is too new. Maximum supported API version is 1.48`.
- **Why it did not reproduce locally:** Compose v2.40.3 and v5.x do not pass env-file values to bake. Checked from scratch with docker-in-docker on Docker 28 / Compose v2.40.3 and Docker 29 / Compose v5.6.0: both build.
- **How it was found:** the job log needs repository admin rights, so the step now prints the tool versions as a `::notice::` and, on failure, the last lines as an `::error::` annotation, which the public checks API shows (`1f8dba0`).
- **Fix** (`9ff85a0`): `DOCKER_API_VERSION` is a comment in `.env.example` (like `LEDGER_PATH`); the runner service will set it in `runner.yml`. With an active value, anyone with a Docker older than API 1.56 who copied `.env.example` would have hit the same failure. chaos-shop was green again at `9ff85a0`.

### 2. Lockfiles

- `uv lock --check` and `uv sync --locked` pass in `contracts/python`, `chaos-shop`, `chaos-shop/cli`, `runner` and `tools/ocp`, before and after the merge.
- `uv lock` in `chaos-shop/cli` changed nothing: Usman's new pyyaml dev dependency belongs to the contracts package's dev group, which a path dependency does not bring into the CLI's lock.
- `flutter pub get --enforce-lockfile` passes. `backend/` has no lockfile yet (skeleton).

### 3. C7 deviations into the contract

`3df4d5d` (only `contracts/telemetry.md`):
- §3.2: one access line per request **except** `/metrics` and `/internal/*` (api, payments, and `/internal/*` through lb).
- §3.2/§3.3: cs-payments request lines have no `route`.

This is Usman's condition for approving C7 (`contract-review-b.md` §1).

### 4. Runner: C5 issue 9, and check 4 (`e50ba21`)

- **Every final answer to an execute claims its ID first.** Before any refused result for an `execute` (checks 1–7 included), the runner sets `ocp:runner:idem:{execution_id}` with SET NX. If the key exists, the message is a duplicate and gets no result.
  - A forged message that borrows a real `execution_id` therefore ends that execution as refused, and the genuine execute arriving later is a duplicate that never runs.
  - A forgery that arrives **after** the genuine execute gets no answer, so it cannot mark a running execution as refused.
  - A refused dry run claims nothing.
- **`catalogue_version` must equal the loaded catalogue's** (C5 check 4) → `VALIDATION_ERROR`, before the action check.
- **Tests:**
  - unit: forged-then-genuine, genuine-then-forged, every refused execute claims its ID, dry run claims nothing, catalogue version;
  - consumer: forged-then-genuine through the stream gives one refusal and one duplicate;
  - live: the real image handled a forgery with a borrowed ID and then the genuine execute. The genuine one was a duplicate, never accepted.
  - Two older tests re-used one message after a refusal and now expect a duplicate. The vector cross-check judges each vector with fresh claims, because the vector's executes share one `execution_id`.

### 5. Merge of `origin/usman` (`9e8dd52`, normal merge commit)

- **Read first:** `docs/checks/contract-review-b.md`. C4 and C6 approved; C7 approved with the change in item 3; review-A issues 1–7 and 9–11 fixed in contracts; issue 8 (more fixtures) later.
- **No conflicts:** `usman` had already merged `tanzeel` up to `b71ed6b` and resolved `compose.yaml` and `week-1.md`. `compose.yaml` now includes `testbed.yml`, `runner.yml` and `observability.yml`.
- **Results on the merged tree:**

  | Suite | Result |
  |---|---|
  | contracts (Usman's suite) | ruff and `mypy --strict` clean; **259 passed**; schemas regenerate with no diff |
  | chaos-shop | ruff and mypy clean; **77 passed** |
  | chaos CLI | ruff and mypy clean; **37 passed** (including the ledger fixture against `releases.yaml`) |
  | `tools/ocp` | ruff and mypy clean; **24 passed** |
  | runner | ruff and mypy clean; **88 passed, no skips**: the vector cross-check runs on the branch now. Coverage **91.7 %**. Live **3/3** |
  | runner output vs C5 models | 40 refusals validate as `RunnerResult`, 8 requests from `publish_signed.py` validate as `RunnerRequest`, and the runner's signing gives the same canonical bytes and signatures as `oncallpilot_contracts.signing` (one-off check in the contracts environment) |
  | proxy security test (TEST-015) | **54/54** |
  | app | `flutter analyze` clean; **90 passed, no skips** (the fixture groups run against the merged C1/C3 fixtures; the new `unit`, `replicas`, `reason`, `lock_reason` fields are ignored by the DTOs, no UI added); generated code unchanged |
  | `ocp up` / `ocp doctor` | 9 services healthy in 30 s (now with `socket-proxy-ro`); doctor all checks passed |

- **On Usman's side, for information:**
  - `handoff.md`, `learn.md` and `next.md` are new files in the repository root (his session notes).
  - `backend/` is still the CI skeleton.
  - The extra fixtures of review-A issue 8 are still to come.

