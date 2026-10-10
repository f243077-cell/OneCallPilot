# Week 3 — checks and progress (Phase 2)

> Evidence for Phase 2 (`phases.md` Phase 2, Weeks 3–4). Stream A (Tanzeel) on branch `tanzeel`.

## Phase 2 progress — Stream A (Tanzeel)

| Task | Status | Date | Evidence |
|---|---|---|---|
| A2.1 Runner: consumer group, signature and expiry verification (shared vector), catalogue and target allowlist, free-form rejection | done (on `tanzeel`) | 2026-10-10 | Below |

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
4. **Coverage not measured:** the runner's ≥ 85 % target (TEST-001) needs pytest-cov, which is not installed.

**Open, for Usman** (C5 issues 9–11 in `contract-review-a.md`): the forged-refusal race, results for unanswerable messages, and the `catalogue_version` rule. The new `.env.example` entries also name the services that need each key (backend-api and backend-worker on his side).
