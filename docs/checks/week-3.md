# Week 3 — checks and progress (Phase 2)

> Evidence for Phase 2 (`phases.md` Phase 2, Weeks 3–4). Stream A (Tanzeel) on branch `tanzeel`.

## Phase 2 progress — Stream A (Tanzeel)

| Task | Status | Date | Evidence |
|---|---|---|---|
| A2.1 Runner: consumer group, signature and expiry verification (shared vector), catalogue and target allowlist, free-form rejection | done (on `tanzeel`) | 2026-10-10 | Below |
| A2.2 socket-proxy-rw config; proxy security tests (RUN-017, TEST-015) | done (on `tanzeel`); one known limitation recorded | 2026-10-10 | Below |
| A2.3 Dry runs for all 4 actions; state fingerprint (RUN-007) | done (on `tanzeel`, not pushed) | 2026-10-10 | Below |
| A2.4a rollback_deploy execute, structural health check (RUN-008, RUN-011, RUN-013, RUN-014, RUN-015) | done (on `tanzeel`, not pushed); restart, scale, clear_cache in A2.4b | 2026-10-11 | Below |

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
1. **`GET /containers/{id}/attach/ws` returns 101** (WebSocket stdio attach). It is a GET under `/containers`, which §2.10 allows, and the proxy cannot block it without also blocking inspect. No command can execute (the containers' PID 1 do not read commands from stdin), but it goes beyond "start, stop, restart". Ruled 2026-10-10: **documented as a known limitation**, with a test that records the behaviour, and added to architecture §7.12 as L6 (`fd7b1cc`).
2. **`kill` is allowed:** §2.10 lists it, RUN-017 and §2.11 do not. The image grants it with `ALLOW_RESTARTS`; the runner does not use it.
3. **The start/stop/restart/kill allow rules ignore the HTTP method,** so e.g. `DELETE /containers/x/stop` reaches Docker. Docker answers 404 (it reads it as a container named `x/stop`), so nothing happens; recorded, no test needed beyond the delete-is-403 test.
4. **`GET /containers/{id}/json` returns every container's environment.** A compromised runner can read other containers' env vars through inspect, which §2.11's limitation note does not mention. Endpoint-level filtering cannot avoid it; keeping secrets in mounted files rather than env (as the FCM key already is) would. For Usman: an architecture-level question, not a runner change. Recorded in architecture §7.12 as L7, an accepted risk for now (`fd7b1cc`); how secrets are stored stays Usman's decision.

**Not covered:** the ro-proxy half of TEST-015 (any POST through `socket-proxy-ro` → 403) waits for Usman's `observability.yml`.

**Gate:** TEST-015 is Gate G2 criterion 3: `docs/checks/gate-g2.md` (created) records it as green for socket-proxy-rw.

### A2.3 — dry runs for the four actions, and the state fingerprint (RUN-007)

**Read first** (2026-10-10): architecture §2.10, §5.6, §5.10, §7.4, §7.5, §7.6 and §9.5; C4 (`actions.yaml`); C5 (`runner.py`, `signing.py`); C1 `DryRunResult`; C8 §2; RUN-006 to RUN-012.

**Rulings (2026-10-10)**, asked before coding because the documents left them open:
1. **Fingerprint as §7.5 says.** It covers the ledger's active release, the ledger's desired replica count, and the sorted names of the target containers that exist and pass the allowlist. Container state is shown in `current` but is not hashed, so a crash loop is not drift.
2. **clear_cache counts under `api`** for rate limit and cooldown. C4 gives it no `service`. The backend must use the same key (tell Usman).
3. **N is the ledger's desired count,** not the running count. RUN-011 says "current replica count"; the fingerprint hashes the ledger value, so both use it. A warning appears when fewer replicas are running.
4. **redis has no ledger record:** release and replicas are null. Its fingerprint is the container name only.

**Built** (`runner/oncallpilot_runner/`):
- **`dry_run.py`:** one preview per action, returned as C1 `DryRunResult`. It holds `would_change`, `current` (release, running replicas, the targets' state and health, `cache_keys`), `preconditions`, `predicted_effect` (at most 300 characters), `warnings` and `state_fingerprint`.
  - **rollback_deploy:** start slots 1..N of `target_release`, then stop the running containers of the active release.
  - **restart_service:** restart the active release's slots 1..N; an exited target is started instead.
  - **scale_service:** start or stop the active release's slots until `replicas` run. The targets are slots 1..max(N, replicas).
  - **clear_cache:** reads `GET http://cs-lb/internal/cache/stats` with `RUNNER_ADMIN_TOKEN`. `catalog` counts as 1 key or 0; `pricing` counts its hash entries. If the stats are unreachable, the preview still comes back, with `cache_keys` null and a warning.
  - **preconditions:** the dry run reads the runner's own counters `ocp:runner:ratelimit:{service}` (ZCOUNT over the last 1800 s, fewer than 3) and `ocp:runner:cooldown:{service}` (EXISTS). Nothing writes them before A2.5.
- **`fingerprint.py`:** SHA-256 of C5's canonical JSON of `{"active_release", "desired_replicas", "target_containers"}`, in lower-case hex. Only the runner computes and compares it.
- **`docker_client.py`:** `DockerReader`, the only Docker access a dry run has.
  - It lists containers by label and returns plain values, never SDK objects, so dry-run code cannot call a write.
  - `SdkDockerReader` uses `DOCKER_HOST` and passes `DOCKER_API_VERSION` as `version=`, so the SDK never calls `/version`.
  - Every listed container must pass `container_allowed` (labels and name regex). `backend-api` carrying the api labels is never a target.
- **`shop_admin.py`:** the stats call, with a 5 s timeout. The token is never logged.
- **`ledger.py`:** `active(service)`, the latest record's release and replicas. A record without them is unreadable.
- **Consumer:** an accepted dry run is answered with an `ok` `dry_run_result` signed with RUNNER_LINK_KEY.
  - If Docker or the ledger cannot be read, the dry run is **refused** with `VALIDATION_ERROR` (fail closed).
  - A Redis error leaves the entry pending for a retry.
  - An execute still runs nothing (A2.4).
- **Start-up:** the runner refuses to start without `DOCKER_HOST`, `DOCKER_API_VERSION` and `RUNNER_ADMIN_TOKEN`. It also refuses a `DOCKER_HOST` that is not `tcp://`, so a socket path is impossible.
- **`.env.example`:** gains a commented `DOCKER_HOST` line (set per service in compose, never in `.env`, for the same reason as `DOCKER_API_VERSION`).

**Tests:**
- **Unit** (`test_dry_run.py`, `test_fingerprint.py`, `test_docker_client.py`, and new startup cases): **158 passed**, coverage **95.4 %**; ruff and `mypy --strict` clean.
  - Every dry run goes through a check that the `FakeDockerClient` recorded no create, start, stop, restart, kill or remove call, that every container is exactly as before, and that the ledger is byte for byte the same. No Redis key is written.
  - **Fingerprint:**
    - the same state gives the same value;
    - a new release, a new desired count, or a target that disappears or loses its labels changes it;
    - container state or health, restart counts, non-targets, unrelated ledger records, a half-written last line and listing order do not.
- **Live** (`tests/live/test_live_dry_run.py`, real testbed and real socket-proxy-rw):
  - The test runs the image as compose will: on `rw_proxy_net` and `chaos_net`, with the ledger mounted **read-only** and no socket.
  - Setup: proxy at debug level, then `chaos inject 2`. At the end: `chaos reset` and the proxy back to `info`.
  - **Order:** rollback_deploy was proved first (api 1.5.0 → 1.4.0: start `cs-api-140-1`, stop `cs-api-150-1`, the expected fingerprint, the same value twice). Then restart (worker, redis), scale (api → 3) and clear_cache (catalog, pricing).
  - **After each batch:** every testbed container has the same state, start time, PID and restart count; the ledger and the runner's Redis keys are unchanged; cs-api logged no `cache cleared`.
  - **RUN-007's acceptance:** the proxy's request log of the dry runs holds **only GET**.
  - The A2.1 live test (3/3) now passes the three new settings.
- **C5 check:** 6 `ok` results and 1 refusal validate as `RunnerResult` with Usman's models, and the signatures verify with `oncallpilot_contracts.signing` (one-off check in the contracts environment).
- **CI:** new job **`runner-live`** in `runner.yml` runs `ocp up` with the TEST-ONLY `.env.example` values, builds the image, and runs `pytest -m live`. It has not run on GitHub yet, because nothing is pushed.

**Memory:** the runner uses 34 MiB of its 128 MiB after the dry runs; socket-proxy-rw uses 17.6 MiB of its 64 MiB. No limits changed.

**Incident during the task:** Docker Desktop's engine VM stopped (every API call returned 500). It was restarted with `docker desktop restart`, then `ocp up`. Nothing was lost.

**For A2.4 and A2.5:**
- §7.6 exempts rollback proposals from the cooldown, but a C5 request does not say whether it comes from a rollback proposal, so the runner cannot apply the exemption. That needs a contract change before A2.5.
- C5 does not say how the worker treats a **refused** dry run (here: Docker or the ledger unreadable). Usman's worker should treat it like `dry_run_unavailable`.

### A2.4a — rollback_deploy execute and the structural health check (RUN-008, RUN-011, RUN-013, RUN-014, RUN-015)

**Read first** (2026-10-11): architecture §2.10, §5.10, §5.11, §7.4–§7.6, §9.5 and §9.6; C5 (`runner.py`: results, `captured`); C6 (`ledger.py`); C1 `StructuralHealth`, `ProbeResult`, `StepStatus`; RUN-008 to RUN-015; the chaos CLI's ledger writer (`chaos_cli/helper.py`).

**Rulings (2026-10-11)**, asked before coding:
1. **Probes:** each target's own `/healthz` on chaos_net (api :8000, worker :8001), plus `http://cs-lb/healthz` for api. §5.10 names only `cs-lb/healthz`, but cs-lb answers that path itself (`location = /healthz`, never forwarded), so on its own it proves nothing about the api release. **Architecture §5.10 should say so** (shared doc: not edited here).
2. **A new release that never becomes healthy** ends `failed`: the runner stops the slots it started, the old release keeps running, no ledger record is written, and `captured.previous_release` is the still-active release. This is contract-review-a issue 14.
3. **Restart count stable:** a passing sample, then a second passing sample 10 s later with the same restart counts, all within 60 s.

**Built** (`runner/oncallpilot_runner/`):
- **`execute.py`:** an execute reaches it only after every A2.1 check and the idempotency claim.
  - **Before any change:** the state is read again exactly as the dry run read it.
    - A different fingerprint → `aborted` / `STATE_DRIFT`.
    - An unreadable state, or target slots that were already missing at the dry run → `aborted` / `VALIDATION_ERROR`.
  - **rollback_deploy:**
    1. start slots 1..N of `target_release` (N from the ledger);
    2. wait until they pass health (one passing sample, at most 60 s);
    3. stop the running containers of the active release (SIGTERM, 10 s timeout);
    4. append the ledger record;
    5. run the structural check.
  - **Never zero healthy replicas:**
    - If the new slots do not become healthy, or a later start fails, the slots already started are stopped again and the old release is never touched.
    - After the old release has been stopped, a failure never stops the healthy new release.
  - **Every write** (start, stop) comes right after a fresh `inspect` and both allowlist checks, and goes by container ID.
  - **restart, scale, clear_cache:** no execute handler until A2.4b. Such an execute ends `aborted` / `ACTION_NOT_ALLOWED` and changes nothing.
- **`health.py`:** the structural check (rulings 1 and 3), returning C1 `StructuralHealth`. Probes return C1 `ProbeResult` with a 3 s timeout.
- **`docker_client.py`:** `DockerControl` adds `inspect`, `start` and `stop` by ID. There is no create, remove, exec, kill or pull.
- **`ledger.py`:** `runner_record()` and `append()`.
  - The record is `kind=rollback`, `deployed_by=runner`, with `replicas` = N and the neutral reason `rolled back to release X` (TB-010).
  - Commit, message, image tag and config hash are copied from that release's own latest ledger record: the runner has no copy of `releases.yaml`, and a rollback target must already be in the ledger history.
  - The record is checked against C6's rules before writing.
  - `append()` writes one whole line in one `write()` on an `O_APPEND` descriptor, then fsyncs. It never uses `O_CREAT`, so it never creates or replaces the file.
- **Consumer:**
  - Each step publishes a signed `progress` result: `validated`, `starting …`, `healthy …` (`running`, then `succeeded` or `failed`), `stopping …`, `recording the rollback in the deploy ledger`, `structural health check` (`running`), `done`.
  - Then one signed `execution_result`: `succeeded` and `failed` carry `health_after.structural` (when it ran) and `captured`; `aborted` carries only `error_code`.
  - A bug during an execution still answers `failed`. Before this change, it fell back to a refusal, which the claimed ID turned into silence.
  - The final result is retried 5 times on a Redis error before the entry is acknowledged. A progress result that cannot be published is only logged.
  - Long waits keep the heartbeat fresh, so the healthcheck stays green.
- **`tests/publish_signed.py`:** a new `--state-fingerprint` option (an execute carried the placeholder `b…b` before, which can only drift). It now prints the entry ID, `request_id` and `execution_id` it published.

**The ledger: who writes it, and the mount** (the runner never wrote it before):
- **Writers:**
  - the chaos CLI, through its helper container, which runs as root: it appends with `O_APPEND`, `chaos reset` replaces the file by rename, and both chown it to uid 10001;
  - the runner, uid 10001 (A2.4a), which only appends.
  - backend-worker will mount it read-only.
- **Ownership on the volume:** the directory is `10001:10001` with mode 0755, and `deploys.jsonl` is `10001:10001` with mode 0644. The runner already runs as uid 10001, so it can append without any permission change.
- **Mount the runner needs:** `deploy_ledger:/ledger` read-write, the **directory**. A file-only mount would keep the old inode after `chaos reset` renames a new file over it, and the runner would then append to a deleted file.
- **What changed:** only the A2.4a live test mounts the volume read-write. The dry-run live test keeps it read-only. No compose file changed: the runner service does not exist in `runner.yml` yet (it waits for copilot-redis), and it will get `deploy_ledger:/ledger` (rw) when added. No ownership or mode changed.
- **Residual write permission, closed (2026-10-11):** the chaos CLI's helper now leaves `/ledger` owned by **root, mode 0755**, on every ledger write (`chaos reset` and every append). `deploys.jsonl` stays `10001:10001` 0644.
  - The runner (uid 10001) can append, but cannot create, rename or delete anything in the directory.
  - **Proved live** (`tests/live/test_live_ledger_access.py`): the runner image, run as its own user with the volume mounted read-write, all capabilities dropped and a read-only root. Append is allowed; create, rename, delete and mkdir are refused.
  - The chaos CLI unit test checks the owners: file 10001, directory 0.
- **Concurrency:** both writers append whole lines with `O_APPEND`, so lines never interleave. **Accepted gap:** a `chaos reset` that lands in the middle of a runner append loses that one record, because the runner's descriptor points at the replaced file. `chaos reset` is an operator action between runs.

**Tests:**
- **Unit** (`test_execute.py`, new probe and SDK cases): **193 passed**, coverage **95.9 %**; ruff and `mypy --strict` clean. `FakeDockerClient` records every call in order, and each test states the exact inspects and writes.
  - **A successful rollback:** inspect 140-1, start 140-1, inspect 150-1, stop 150-1. The steps come in order, the structural check passes, `captured.previous_release` is 1.5.0, and the ledger is unchanged except for one valid runner record.
  - **A drifted state** (a ledger record since the dry run, or a missing slot) aborts with `STATE_DRIFT`: no write, no step, ledger unchanged.
  - **A new release that stays unhealthy, exits or restarts** (and one whose probe fails): writes are exactly start 140-1 then stop 140-1, 150-1 keeps running, no ledger record, and the runner gives up after 60 s.
  - **Also covered:**
    - a start failure on slot 2 of 3 undoes slot 1;
    - a container that fails the allowlist at write time is never touched;
    - a stop failure or a ledger failure after the switch keeps the new release running;
    - a structural check that fails after the switch reports `failed`;
    - the stability window catches a restart.
  - **Through the consumer:**
    - signed progress then exactly one signed result;
    - a replay of the same signed execute → `Duplicate`, with writes made once;
    - forged, unapproved, free-form, expired and wrong-key executes never reach Docker;
    - a bug answers `failed`;
    - `aborted` carries only its code;
    - the final result is retried and gives up unacknowledged.
  - **Ledger writer:** never creates the file; refuses a record that would break C6.
- **Live** (`tests/live/test_live_execute.py`, real testbed and proxy, ledger mounted read-write):
  - After `chaos inject 2`, a large checkout through cs-lb fails.
  - A signed dry run gives the fingerprint, then `publish_signed.py` publishes the signed execute twice (`--repeat 2`).
  - **Result:** `succeeded`, with every step in order and every message signed with RUNNER_LINK_KEY. The structural check passed: `cs-api-140-1` running and healthy, every probe OK.
  - **Recovery:** 140-1 is running and 150-1 exited, and the large checkout succeeds again.
  - **Ledger:** unchanged except for one record that **validates with C6 `DeployRecord`** (`rollback`, api, 1.4.0, 1 replica, `runner`).
  - **Replay:** the second message was a `duplicate`, executed once.
  - `chaos reset` restores the testbed at the end.
- **Live, all modules:** **6/6** (A2.1 ×3, A2.3 ×2, A2.4a ×1).
  - The A2.1 test now expects its accepted execute to end `aborted` / `VALIDATION_ERROR`: that stack has no proxy, so the state cannot be read, and it fails closed.
  - All live tests take the Docker API version from the host daemon.
- **C5 check:** 13 progress results, 1 `succeeded`, 1 `failed` and 1 `aborted` validate as `RunnerResult` with Usman's models, and every signature verifies with `oncallpilot_contracts.signing` (one-off, in the contracts environment).
- **CI:** `runner-live` already runs `pytest -m live`, so the execute test runs there too.
  - It sets no `DOCKER_API_VERSION`: the stack gets none from `.env.example`, and the live tests read `docker version` on the runner host (1.48 on GitHub).
  - Not run on GitHub yet: nothing is pushed.

**Memory:** the runner uses 33 MiB of its 128 MiB after the rollback. No limits changed.

**Incident:** after midnight the Docker engine was still starting when the A2.1 test ran (3 errors: no Docker pipe). Once it answered, `ocp up` brought back 9 healthy services and the whole live suite passed.

**Open for A2.4b and A2.5:**
- **Rate limit and cooldown counters (A2.5):** an execute does not yet check or write `ocp:runner:ratelimit:*` and `ocp:runner:cooldown:*`.
- **Architecture §5.10:** should name the per-container probes (ruling 1).

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


## Phase 0 close-out, part 3 (2026-10-10): after the merge into `main`

- **`main`:** PR #1 merged as `8942520` (merge commit). It contains `tanzeel` and `usman`, with C1–C9. Its tree is identical to `7d576ac`, the tree tested in part 2.
- **CI on `main` (`8942520`):** backend, contracts, migrations, chaos-shop, runner (`runner` and `runner-proxy`), mobile: **all green**.
- **Tag:** annotated `contracts-v0.1.0` on `8942520`, pushed; `contracts/VERSION` is `0.1.0`.
- **`tanzeel` synced:** fast-forward to `8942520`.
- **Answers to the A2.2 questions:**
  - attach/ws and the env exposure of inspect are in architecture §7.12 as L6 and L7 (`fd7b1cc`, docs only). L7 is an accepted risk for now; how secrets are stored stays Usman's decision.
  - socket-proxy-rw has `mem_limit: 64m` (`7012459`); it uses about 18–22 MiB.
  - A2.2 deviations 1 and 2 are accepted; the ro half of TEST-015 waits for Usman's observability compose.
- **Full local run on `tanzeel` (`7012459`):**

  | Suite | Result |
  |---|---|
  | Lockfiles | `uv lock --check` and `uv sync --locked` pass in `contracts/python`, `chaos-shop`, `chaos-shop/cli`, `runner`, `tools/ocp`; `flutter pub get --enforce-lockfile` passes |
  | contracts | ruff and `mypy --strict` clean; **259 passed**; schemas regenerate with no diff |
  | chaos-shop / chaos CLI / `tools/ocp` | ruff and mypy clean; **77 / 37 / 24 passed** |
  | runner | ruff and mypy clean; **88 passed**, coverage **91.7 %**; live **3/3** (image rebuilt) |
  | proxy security test (TEST-015) | **54/54** (with the 64 MiB limit) |
  | app | `flutter analyze` clean; **90 passed** |
  | `ocp up` / `ocp doctor` | 9 services healthy in 34 s; doctor: all checks passed |

- **Memory:** the 9 running containers use about 283 MiB. socket-proxy-rw uses 17.8 MiB of its 64 MiB. socket-proxy-ro (Usman's) uses 21 MiB and has no limit yet. Windows had 3.5 GB free.
- **Phase 0 status table:** `week-1.md`, "Phase 0 status (2026-10-10, after PR #1)".
