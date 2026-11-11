# Learn — OnCallPilot in plain words

> A plain-language guide to what happens in this project and why, written for someone new to it (`CLAUDE.md` §11.4). Sections 1–3 explain the basics. Section 4 is a dated log: a new entry is added every session, and old entries are never rewritten.

---

## 1. The project in one minute

When a website breaks at 3 a.m., the on-call engineer is woken up and usually spends the first 10–30 minutes just working out **what** broke: digging through logs, comparing graphs, checking what was deployed recently. OnCallPilot is a helper for that moment.

1. A **detector** notices that something is wrong, for example a sudden burst of errors.
2. An **AI agent** on the server investigates. It can only **read**: logs, metric graphs, the list of recent deploys, container health, and runbooks (how-to documents).
3. It writes up to **three guesses** ("hypotheses") about the cause. Each guess points at the evidence it used, by short labels such as `E2` or `E4`.
4. It **double-checks itself**. Then it either says "I'm not sure, a human should look" (an *escalation*) or suggests **one** fix from a short, fixed menu: restart a service, add replicas, roll back a release, or clear a cache.
5. The engineer reads the evidence on an **Android phone** and taps **Approve** (with a fingerprint for riskier fixes) or **Reject**.
6. A separate, locked-down program, the **runner**, applies the fix and then checks that the system really recovered. If it did not, it offers to undo the fix.

The AI never changes anything by itself. A human always approves, and the runner checks everything again before it acts.

We test this on **Chaos Shop**, a small fake online shop that we break on purpose in 8 different ways. Then we measure whether people find the cause faster with OnCallPilot than without it: 8 faults × 3 repeats × 2 modes = **48 timed runs**. The headline result will be a sentence like "the copilot cut the median time-to-diagnosis from X to Y minutes, with zero unsafe actions."

## 2. Who builds what

| | **Usman** (Stream B, "the brain") | **Tanzeel** (Stream A, "the hands and the screen") |
|---|---|---|
| Builds | the backend API, the AI agent, the database, the monitoring stack (Prometheus, Loki, Grafana), the benchmark tools | the phone app, Chaos Shop, the runner |
| Main folders | `backend/`, `supabase/`, `infrastructure/observability/`, `benchmark/` | `mobile/`, `chaos-shop/`, `runner/` |

Some things are **shared**: `contracts/`, `docs/`, `compose.yaml`, `.env.example`, and the CI workflows in `.github/`.

The two halves only meet through **contracts** (next section). That is what lets two people build at the same time without waiting for each other.

## 3. Ideas you need, in simple words

- **Contract.** An agreed description of a message that one part of the system sends to another, for example "an incident has an id, a service, a severity, …". Think of it as a paper form both sides promise to fill in the same way. Because the forms are agreed first, Tanzeel can build the app against them while Usman builds the backend, and the two fit together later. There are ten (C1–C10); each has a *drafter* who writes it and a *reader* who checks it.
- **Pydantic model.** Python code that describes one of those forms and **rejects** anything that does not fit: a wrong type, a missing field, or an unexpected extra field (`extra="forbid"`).
- **JSON Schema.** The same form written as a JSON file. It is generated from the Pydantic model by a script, never written by hand, so other languages (such as Dart in the app) can check messages too.
- **Fixture.** A filled-in example of a form, saved as a file. Tests use fixtures, and the app's *mock mode* replays them so the app works before the real backend exists.
- **HMAC signature.** A tamper-proof seal on a message, made with a secret key that only the sender and receiver know. If anyone changes even one character, the seal no longer matches and the message is refused. OnCallPilot uses two keys, so the part of the backend that talks to the AI can never forge an "execute" order.
- **Docker and Docker Compose.** Docker runs each program in its own box (a *container*). Compose is a file that lists which containers to run and how they connect. Our root `compose.yaml` uses `include:` lines so that each owner keeps their own file under `infrastructure/compose/`.
- **Socket proxy.** A doorman between a program and Docker. The *read-only* proxy (`socket-proxy-ro`, Usman's) only lets "look" requests through. The *read-write* proxy (`socket-proxy-rw`, Tanzeel's) only lets start, stop, and restart through. Nothing can create, delete, or run commands inside containers.
- **Git branch.** A separate line of work in the same repository. `main` is the shared final version. `tanzeel` is Tanzeel's line of work, and `usman` is Usman's. Usman's line started as a copy of Tanzeel's, so it already contains his work; new work from Tanzeel is brought in by *merging*.
- **OpenAPI (`openapi.yaml`).** A standard file that lists every web address (endpoint) of the backend: what you send to it, what comes back, and what errors are possible. The app and the backend both build against it.
- **WebSocket.** A connection that stays open, so the server can *push* news to the phone the moment it happens ("new evidence", "proposal ready") instead of the phone asking again and again.
- **Fingerprint.** A short code computed from the exact contents of something. If anything changes, the fingerprint changes. The phone sends back the fingerprint of the proposal it showed, so the server can refuse an approval for something that changed in the meantime.
- **Idempotency key.** A random ID attached to an approval attempt. If the phone sends the same attempt twice (a double tap, or a retry after the network dropped), the server sees the same key and does the action only once.
- **Test vector.** A worked example with the right answer written down, for example "this message, signed with this test key, gives this signature". Both sides check their code against it, so their signing can never quietly drift apart.
- **Timeline fixture.** A whole incident, from "opened" to "resolved", written as a list of timed events plus the final state. The app's mock mode plays it like a recording.
- **Discriminated union.** A message that can be one of several kinds, with a field that says which kind it is (for example `"event": "proposal.created"`). The checker reads that field first, then checks the rest against the right form.

### Docker, in more detail

**The problem it solves.** A program needs the right version of Python, the right libraries, and the right settings. Installing all of that by hand on every laptop is slow, and two projects can need different versions ("it works on my machine"). Docker packs a program **with everything it needs** into one standard box, so it runs the same on Usman's laptop, Tanzeel's laptop, and GitHub's CI. The name comes from shipping containers: one standard box that any ship, crane, or truck can carry, whatever is inside.

**Images and containers**, the two words that matter most:

| Word | What it is | In this project |
|---|---|---|
| **Image** | A frozen, read-only package: a small Linux file system, the runtime (such as Python 3.12), the libraries, and the program. It is built from a recipe file called a **Dockerfile**. | `chaos-shop/Dockerfile` builds the image `chaos-shop/api:1.4.0` |
| **Container** | A running copy of an image, like an object made from a class. Many containers can run from one image, and deleting a container does not change the image. | `cs-api-140-1` runs from `chaos-shop/api:1.4.0`; task A1.2 adds four more copies (slots) of it |
| **Tag** | The version label after the colon. We always pin it and never use `latest`, so everyone runs exactly the same thing (`CLAUDE.md` §4.8). | `postgres:17.11-alpine` |
| **Registry** | An online store that Docker downloads images from. | Docker Hub, `lscr.io` |

**Not a full virtual machine.** A virtual machine runs a whole second operating system. Containers share the host's Linux kernel, so they start in about a second and use little memory: Tanzeel measured the whole Chaos Shop baseline at about 215 MiB. Windows has no Linux kernel, so Docker Desktop runs one small Linux virtual machine (WSL 2), and every container runs inside it:

```text
Windows laptop
└── WSL 2: one small Linux virtual machine
    └── Docker Engine: starts, stops, and watches containers
        ├── cs-postgres      (image postgres:17.11-alpine)
        ├── cs-api-140-1     (image chaos-shop/api:1.4.0)
        └── socket-proxy-ro  (image lscr.io/linuxserver/socket-proxy:3.4.6-r0-ls101)
```

**A container sees only what it is given.** That is what makes Docker useful for safety:
- **Environment variables:** settings and secrets passed in at start, such as `CS_POSTGRES_PASSWORD`. Each service gets only the ones it needs; for example, the runner never gets `DATABASE_URL`.
- **Volumes:** folders or files from outside, mounted inside the container. `cs_postgres_data` keeps the shop's data when the container restarts.
- **Networks:** containers on the same Docker network reach each other by name (`cs-api-140-1` finds the database simply as `cs-postgres`). Containers on different networks cannot reach each other at all, and `internal: true` also cuts a network off from the internet. This is how the trust zones of architecture §3 are built: only the runner and `socket-proxy-rw` share `rw_proxy_net`.
- **Ports:** nothing outside can reach a container unless one of its ports is *published*. Only `backend-api:8000` is published to the Wi-Fi, so the phone can reach it.

**Health and restarts.** A *healthcheck* is a small command Docker runs every few seconds (for Postgres, `pg_isready`) to mark a container healthy or unhealthy. Other containers can wait for that (`depends_on: condition: service_healthy`). A *restart policy* says what happens when a container stops, and that matters for the faults: `cs-redis` has none, so when scenario 4 stops it, it stays stopped until someone fixes it.

**The Docker socket is the master key.** Docker is controlled through one file, `/var/run/docker.sock`. Whoever can use it can start, stop, create, or delete any container, which means control of the whole machine. So in OnCallPilot only the two socket proxies may touch it, and they act as doormen. `socket-proxy-ro` lets only "look" requests through. `socket-proxy-rw` lets only start, stop, and restart through. That is also why every api and worker version exists in advance as a stopped *slot* container: the runner can switch slots on and off, but it can never create a new container (ADR-04).

**Docker Compose** describes many containers in one YAML file, so one command starts them all in the right order (`docker compose up`) and one stops them (`docker compose down`). Each entry under `services:` is one container: its image, settings, volumes, networks, and healthcheck. A *profile* is a group switch: `socket-proxy-ro` has `profiles: [obs]`, so it starts only with `--profile obs`. Our root `compose.yaml` holds little more than `include:` lines. Each owner keeps their own file (`testbed.yml` and `runner.yml` for Tanzeel; `observability.yml`, and later `copilot.yml` and `test.yml`, for Usman), so the two of them rarely edit the same file (`CLAUDE.md` §8.5). Later, `ocp up` will run Compose for you, reset Chaos Shop, and wait until everything is healthy.

---

## 4. Session log

### 2026-10-09 — Session 1: getting set up

**Where things stood.** Tanzeel had already pushed a lot to his branch `tanzeel`: the folder layout, the CI workflows, three contracts (C4 the action menu, C6 the deploy history file, C7 what Chaos Shop's logs and metrics look like), the start of the phone app, and the first running version of Chaos Shop. On Usman's laptop there were only five loose document files, and they were **older** than Tanzeel's: they still said his laptop has 20 GB of RAM, which he had corrected to 16 GB.

**What we did, step by step.**
1. **Checked that nothing would be lost.** Git can compute a fingerprint (a *hash*) of a file. The five local files had exactly the same fingerprints as the files in Tanzeel's first docs commit, so they were safe to replace. A backup copy was kept anyway.
2. **Made the folder a git checkout** and created the `usman` branch from the latest `tanzeel`. Now Usman has all of Tanzeel's work and the corrected documents.
3. **Added a safety guard.** A small script in `.git/hooks/pre-push` runs before every push and refuses anything except the `usman` branch, so a slip of the keyboard cannot push to `main` or to Tanzeel's branch. It lives only on this laptop; it is not part of the shared repository.
4. **Wrote the house rules into `CLAUDE.md`** (section 11): work only on `usman`, never push to `tanzeel` or `main`, bring in Tanzeel's work by merging, and keep these three notes files up to date.

**Why merge and not "rebase"?** Both bring in Tanzeel's new work. Rebasing rewrites the history of `usman`, which breaks the copy already on GitHub. Merging only adds to it, so it is the safe choice for a branch other people can see.

**Then we wrote Usman's six contracts.** Tanzeel had asked for C1, C2, C3, C5, C8, and C9. In plain words:

| Contract | What it agrees | Everyday picture |
|---|---|---|
| C1 domain models | What an incident, a piece of evidence, a hypothesis, a proposal, and an execution look like | The standard forms everyone fills in |
| C2 REST API (`openapi.yaml`) | Every address the phone can call, and its answers and errors | The menu of a restaurant, with what each dish contains |
| C3 WebSocket | How live updates reach the phone, in order, and how a phone that lost signal catches up | A live news ticker that can replay what you missed |
| C5 runner messages | The exact "do this" and "done" messages between the backend and the runner, and how they are sealed | Sealed envelopes between two offices |
| C8 approval protocol | The steps to approve safely: get a one-time code, confirm with a fingerprint on the phone, approve | A bank transfer that needs a one-time code |
| C9 push payload | The tiny notification a phone receives when the app is closed | A doorbell: it only says "come look", never the details |

Each contract was written as Python models, so a machine can check every message. Then a script turned each model into a JSON Schema file, so the Dart app can check messages too. There are 210 new automatic tests, and all 246 tests pass.

**We wrote sample data (fixtures) for the walkthrough.** There are three full incident stories:
1. *bad deploy, fixed*: a new release breaks checkout; the AI suggests rolling back; approved; fixed.
2. *slow payments, escalated*: an outside company is slow; nothing on the menu can fix that; the AI stops and asks a human.
3. *scaling did not help, so it was undone*: three copies of the API did not fix the slowness; the system offers to undo the change; approved; resolved.

The tests replay each story and check that it makes sense: each step follows the allowed order, every piece of cited evidence exists, and every action matches Tanzeel's action menu (C4).

**Why the "sealed envelope" matters (C5).** The runner is the only part that can change anything, so it must be sure an order is real. Every message gets a signature made with a secret key. The part of the backend that talks to the AI has **no** copy of the "execute" key, so even if the AI were tricked, it could not forge an order. We saved worked examples (test vectors) with fake test keys, so Tanzeel's runner and Usman's backend can prove they seal envelopes the same way.

**The read-only doorman (socket-proxy-ro).** Usman's monitoring tools need to *look at* Docker containers but must never change them. `socket-proxy-ro` sits in front of Docker and lets only "look" requests through. Tanzeel found that this proxy image also opens two extra "Podman" doors by default (`LIBPOD_PING`, `LIBPOD_VERSION`), so we closed them, as he did for his read-write proxy. It is written down but not started yet, because Docker is not installed on this laptop.

**What is still waiting, and why.** Three checks (S0.7) need things only Usman can set up: Docker Desktop (to test the doorman), a Gemini key (to test the search "embeddings"), and the Supabase projects (to test login). The steps are in `handoff.md`.

**Tanzeel kept going, too.** During the day he pushed task A1.2 to his branch:
- **`cs-lb`**, a load balancer (nginx) that spreads the shop's traffic across however many API copies are running;
- **all 12 "slot" containers**: every API and worker version, created in advance and left stopped, so the runner only ever switches them on or off.

He also left Usman a decision. In two small places his code differs from the telemetry contract (C7): admin calls are kept out of the logs on purpose, and the payments service's log lines have no `route` field. Usman decides whether the contract is changed to match.

Git checked that his new work merges cleanly into `usman`; the merge itself is the first step of the next session.

**A to-do list for the next session.** We wrote `next.md`: an ordered list of exactly what the next Claude session should do. It covers what to check first, which questions to ask Usman, what each check (S0.7) needs, and what must wait. A new session starts with no memory of this one, so these files are how the work carries on without anything being lost.

### 2026-10-10 — Session 2: reviewing each other's contracts in writing

**Where things stood.** Tanzeel had read Usman's contracts and written down 11 problems (`docs/checks/contract-review-a.md`). He asked Usman to read his three contracts (C4, C6, C7), answer each problem, and push, but **not** to merge into `main` yet. Meanwhile he had pushed 44 more commits: the load balancer, all faults working, the first screens of the phone app, and the start of the runner.

**What we did, step by step.**
1. **Brought his work in.** Git merged his 44 commits into `usman` with no conflicts, because the two of them mostly work in different folders.
2. **Read his contracts.** The action menu (C4) and the deploy history (C6) are fine. The telemetry contract (C7) is fine too, except that it still promises a log line for *every* request, while his code skips admin requests on purpose. We asked him to write that into the contract, so the document matches the code.
3. **Fixed the problems he found in Usman's contracts.** The two important ones, in plain words:
   - *"Cut to 4 KB" broke its own checker.* Live updates to the phone shorten big evidence to 4 KB. Cutting text in the middle produces broken data, which the checker then rejects. Now the rule is to drop whole items from the end of a list (whole log lines, whole graph points) until the evidence fits. The data stays well-formed, and a flag says some was left out.
   - *The deploy history the AI sees was missing two fields*: how many copies were running, and why the deploy happened. Without them, a "scale" record says nothing useful.

   Smaller fixes: graphs now say their unit (seconds, bytes, …) so the app can label the axis; only the API and the worker have graphs; the settings screen gets a reason when it is locked; the sample incidents now use the testbed's real version history and the real error of the broken release.
4. **Wrote three safety rules for the runner** that came from Tanzeel building it. The most interesting: if someone forges a "refused" answer using a real job ID, the real job must not run afterwards. Now every final answer "uses up" the job ID first, so a forged or late copy of the order is ignored.
5. **Found that a change of ours broke a check of his.** Our contracts package gained a test-only library (`pyyaml`). Tanzeel's chaos tool depends on that package, so its lockfile (the file listing exact library versions) went out of date, and CI's strict install would fail. We refreshed that file, as Usman asked, and told Tanzeel to do the same in his merge.

**Why not just push everything to `main`?** `main` is the shared, always-working version. The team's rule is that changes reach it only through pull requests whose automatic checks (CI) pass, with contracts in their own small PRs. Tanzeel also asked to wait. One of his CI checks is red right now, and pushing straight to `main` would skip all of these safety steps.

### 2026-10-10 — Session 3: getting ready to merge, and why Docker won't start yet

**A rule changed.** Until now this laptop could push only the `usman` branch. Bringing work into `main` "the proper way" means **pull requests**: you push a small branch (for example `contract/c1-domain`), open a PR on GitHub, let CI test it, and merge it only when everything is green. Usman agreed, so `CLAUDE.md` now allows his own PR branches, and the safety script was updated to match. Pushing straight to `main` or to Tanzeel's branch is still blocked.

**Docker is installed, but its engine isn't running.** Docker on Windows needs a small Linux system underneath it: WSL 2, the "Windows Subsystem for Linux" (see "Docker, in more detail" above). This laptop doesn't have WSL 2 yet, so Docker Desktop has nowhere to run its containers. Installing it takes one command in an administrator terminal (`wsl --install --no-distribution`) and a restart. The steps are in `handoff.md`.

**Is Phase 0 finished? Not yet.** Phase 0 means "agree on everything before building". The paperwork part is done: both developers have read and reviewed each other's contracts, the samples pass the tests in Python and in the phone app's language (Dart), and the phone reached the laptop. Three kinds of things are still open:
- **Usman's setup:** Docker running, a Gemini key, and the Supabase projects.
- **The three checks (S0.7) that need that setup:** the read-only proxy, the embeddings, and the login.
- **The merge into `main`,** which waits for Tanzeel's red CI check to be fixed and for his go-ahead.

### 2026-10-21/22 — Cloud session 1: working alone, finishing Phases 2 and 3, and the first real run

**What changed in how we work.** Usman left, so Tanzeel is the only developer. Work now goes straight to `main` (no branches, no pull requests), but only after the same checks CI runs have passed on the machine first. The session ran in a cloud container while the laptop was off.

**What got built, in plain words.**
- *The backend side* (Usman's old stream): the approval rules (one-time challenge, fingerprint, rate limits), turning an approval into a signed order for the runner, checking that the fix really worked and offering an undo if not, push notifications, a second AI provider (Claude) behind the same interface, the benchmark tools, and graphs of how the copilot itself behaves.
- *The phone app*: the screen where you review and approve an action (fingerprint for risky ones), the live connection to the server that reconnects by itself and catches up on what it missed, a red banner and a disabled Approve button whenever the screen might be out of date, the execution screen with "Review rollback", audit history, settings, and a live log view.
- *Runbooks*: eight short how-to documents the AI can search ("how to roll back a release", "when to escalate"). They are written generically so they don't give away the benchmark's answers.

**Running the whole system for real found five bugs no test had caught.** Unit tests check pieces with fakes. Starting everything together (testbed, monitoring, server, worker) showed what the fakes hid:
1. *The server could not accept live phone connections at all.* The web server program needs a small extra library for WebSockets; the tests use a test client that doesn't, so they never noticed.
2. *The alarm could not learn what "healthy" looks like.* "Errors per second divided by requests per second" returns *nothing* (not zero) when there are no errors, so the detector only ever learned from bad periods. Now it reads 0 when nothing fails.
3. *The test mode without an AI key crashed instead of handing the incident to a human.*
4. *`chaos reset` caused a false alarm*, because it restarted containers before writing "a reset is happening" to the deploy history, which is what tells the alarm to stay quiet.
5. *The worker wrote a log line for every web request it made*, about four a second.

**Two lessons.**
- A shared folder can be checked by more than one test suite. The runbooks passed the backend's check but failed the testbed's "no giveaway words" check (it bans words like "bad" and "leak"), and that broke CI once. Now every suite that reads a folder runs before a push.
- An alarm that starts up in the middle of a problem learns the problem as "normal". The benchmark keeps the worker running, so this only matters after a restart: then wait for a full healthy warm-up before injecting a fault.

**Words.**
- *Idempotency key*: a random ID sent with an approval. If the network drops and the phone sends it again, the server sees the same ID and doesn't run the action twice.
- *Stale*: the phone might be showing old information (lost connection, nothing heard for 30 s, proposal expired, or a newer version on its way). Approving is blocked while stale; rejecting is always allowed.
- *Baseline*: what "normal" looks like for a signal, learned over time; an alarm fires when a value is far from it.
- *Warm-up*: the first 3 minutes after a start or a reset, when the detector learns and never alarms.

### 2026-11-02 — Local session: one key for the AI, and the real database

**Where things stood.** Cloud sessions had built almost everything that needs no outside accounts. What was left needed a database account (Supabase), an AI key, and the phone.

**What we did.**
1. **Switched the AI to OpenRouter.** OpenRouter is a single door to many AI models. The plan said "use Google's Gemini with a Gemini key"; we now reach the *same* Gemini model through OpenRouter with one key. Before writing code we sent real test requests with the agent's own question formats, so we knew it works. Written down as ADR-23.
2. **Found two surprises in those tests.** The model "thinks" before answering, and its thinking must be sent back unchanged in the next turn or it loses track. And the search-vector service ignores the hint "this is a question" vs "this is a document"; search still found the right runbook.
3. **Connected the real database.** The script applied the table definitions, created the backend's own database login with a random password (written only to the private `.env` file), and checked that sign-ins work, that strangers cannot sign up, and that the audit log cannot be edited.

**Words.**
- *OpenRouter*: a service that forwards a request to the AI provider you name, with one bill and one key.
- *Embedding*: a list of 768 numbers that captures what a text means; texts with similar meaning get similar numbers. Runbook search compares them.
- *JWKS*: the public keys Supabase publishes; the backend uses them to check that a sign-in token is genuine without asking Supabase each time.
- *Connection pooler*: a gatekeeper in front of the database that shares a few connections among many clients.

### 2026-11-02 (evening) — The real stack, and a clock that was 13 hours wrong

**What worked.** The backend ran for the first time against the real Supabase database and answered with a real sign-in. The runner now starts with the rest of the stack, and a test checks its safety settings in the real compose file. A new command, `ocp smoke`, plays the whole story: break the shop, wait for the copilot's suggestion, approve it the way the phone would, and check that the shop is fixed.

**What went wrong, and why.** Windows was set to the US Pacific time zone while showing Pakistan time, so the computer believed it was 13 hours later than it was. For a while the containers believed it too. Programs that remember things "until a time" were confused: the metrics database threw away every new measurement as "older than what I already have", and the load balancer kept an old address for the api because it thought its notes were still fresh. Setting the time zone right fixed the clock; restarting the stack clears the confused programs.

**Words.**
- *Time zone vs clock*: the clock shows local time; the time zone tells the computer how far that is from world time (UTC). A wrong zone with a "right-looking" clock makes world time wrong.
- *DNS cache*: a remembered "name → address" answer, kept until it expires.
- *Smoke test*: one quick run through the whole system to see that nothing is badly broken.

### 2026-11-03 — Recording the real shop, and an alarm that would not ring twice

**What we did.** The agent's regression tests used made-up tool answers. A new script now breaks the real shop in each of the 8 ways, waits for the copilot to notice, and saves what the logs, metrics, deploy history and containers really said. The tests replay those saved answers, so they check the agent against real data without needing the shop running.

**What the recording found.**
1. **The alarm stayed "on" after a reset.** Once a signal (say, slow api responses) raised an alarm, the detector waited for 3 calm minutes before it would raise that alarm again. After a reset, the first 150 s are ignored, so the 3 minutes could not finish before the next fault was injected at 240 s. Two runs in a row that break the api the same way got only one alarm. Now a reset starts the 3-minute count at once (ADR-25).
2. **The reset could not get past a traffic flood.** After the "traffic spike" scenario, the reset restarted the api while the fake customers were still flooding it, so the api never looked healthy in time and the reset gave up before it could tell the fake customers to calm down. Now it calms them first.
3. **The planted message in scenario 8 is hidden from an error-only search.** The fake attacker's text lands in a normal (INFO) log line and in a detail field that the log tool does not return. If the agent only reads errors, it never sees the trap.

**Words.**
- *Recording / fixture*: saved real answers that a test replays instead of asking the live system.
- *Recovery*: the detector deciding a problem is over, after its signals stay normal for 3 minutes.
- *Line endings*: Windows ends text lines with two characters (CRLF), Linux with one (LF); the repo uses LF, so files written on Windows must be written as LF.

### 2026-11-03 (evening) — The first push on a real phone

**What we did.** The phone app now asks for permission to show notifications, tells the backend its "address" for notifications (a token from Firebase), and opens the right incident when a notification is tapped. The backend got the Firebase key as a read-only file. Then we broke the shop on purpose: 24 seconds later the phone buzzed with "New incident", and a tap opened it.

**What went wrong on the way.**
1. **The laptop's address changed overnight.** The router hands out addresses and gave the laptop a new one, so the app was knocking on the old door. A reservation on the router keeps the address fixed.
2. **"Reconnecting" forever.** The connection was fine, but right after connecting the app reloads its list, and that reload asked for something that depended on the connection itself: a loop the app's toolkit refuses in test builds. The app never counted itself as "caught up". Now the reload asks in a way that does not create the loop.
3. **A security test caught a change I forgot to test.** The backend's mounted folders are pinned by a test; adding the key folder broke it for one push. The test now also checks that no other service can see that folder.

**Words.**
- *Push token*: the address Firebase gives one app on one phone; the backend sends notifications to it.
- *DHCP reservation*: telling the router to always give one device the same address.
- *Circular dependency*: A needs B and B needs A, so neither can be built first.

### 2026-11-04 — Two people on one branch

**What changed.** Usman is back, and both of us now push to `main`. Usman has OpenRouter credit, so he takes the tasks that need the real AI model. Tanzeel keeps the phone, the testbed and everything else.

**Why the rules changed.** With one person, `main` only moves when you push. With two, the other person may have pushed while you worked. So before each push you first pull with `--rebase`: git puts your new, unpushed commits on top of theirs. Then you run the checks again, because their change and yours together can break something that neither broke alone. A rejected push is not an error to force through; it means "pull first".

**Words.**
- *Rebase (on pull)*: replaying your own not-yet-pushed commits on top of the newest `main`, so the history stays one straight line.
- *Force-push*: overwriting the shared history with yours; it can delete the other person's work, so it is never used here.

### 2026-11-04 (evening) — A phone that was asleep, and an alarm about the past

**What we did.** We closed the app completely, locked the phone and broke the shop. The phone still got the notification, and tapping it started the app straight on the right incident. That is the case that matters on a real on-call night.

**What we found.** The same test raised a second, wrong alarm: "worker restarts". Nothing was wrong with the worker. When the laptop's Docker started, the deliberately broken worker from scenario 6 started too and kept crashing until the reset stopped it. The detector looks back 5 minutes for crashes but only stays quiet for 3 minutes after a reset. So between minute 3 and minute 5 it "saw" a crash from before the reset. In the benchmark, that would add a fake incident after every scenario 1 or 6 run.

**The fix.** The detector now treats everything containers do during a reset's first 150 seconds as part of the reset, not as a new problem. Crashes after that still count. Two tests check both sides.

**Words.**
- *False positive*: an alarm when nothing is wrong.
- *Window*: how far back a check looks (here, 5 minutes of container history).

### 2026-11-04 (late night) — A script for the demo, a check of the runbooks, and one rule made consistent

**Demo script.** The final video is two minutes long, but the real run takes about four. So the script says exactly what to film, and every cut shows how much time it skipped. Nothing the AI wrote may be edited.

**Runbooks.** These are the short guides the AI can search. We checked that they promise only actions that really exist, with the right risk level, and that they do not secretly contain the answers to the 8 test scenarios. If they did, the benchmark would be cheating.

**One rule, two descriptions.** Some documents said "delete the phone's record" when a phone signs out or Firebase says its address is dead. The database rules say the backend may never delete anything. The code followed the database rules and switched the phone off instead. We kept that. Deleting would also break the approval history, because each approval remembers which phone gave it. Now every document says "disabled".

**Words.**
- *Storyboard*: a shot-by-shot plan for a video.
- *Patch version* (0.2.0 → 0.2.1): a change to the wording of a contract that changes no data or behaviour.
- *Foreign key*: a column that must point at an existing row in another table.

### 2026-11-08 — The first full loop with a real AI, and why it took five tries

**What happened.** The smoke test runs one fault from start to finish: the shop breaks (a bad release), the detector notices, the AI investigates and proposes a fix, a person approves, the system rolls the release back, and the shop recovers. On Usman's laptop it finally passed, about 4 minutes from fault to recovery. Each failed try before that taught something:
- *A slow computer can look like a broken one.* A game was using the CPU, so health checks ran out of time.
- *Timing between parts.* The detector sent its alarm twice. The second alarm added a new clue while the AI was working, and the AI was not shown it. When the AI mentioned that clue, the code thought it was invented and threw away the right answer. Now the AI is shown clues that arrive during its work.
- *Rules the AI never heard.* The code halves the confidence of an answer that does not cite the right kind of evidence: for "a bad release", the deploy history. Nobody told the AI that rule. Version 2 of the instructions now does, in the words the AI sees. A review found that the first draft explained only half the rule, which would have hurt other faults, so it was fixed before anyone else saw it.
- *Old evidence.* The AI's tools look back 30 minutes. A fault from the previous try was still in that window and looked like proof against the right answer. The fix for testing is to wait 30 healthy minutes; the benchmark needs a team decision.

**Words.**
- *Smoke test*: one quick end-to-end run that shows the whole system works at all.
- *Look-back window*: how far into the past a tool searches (here 30 minutes before the incident).
- *Prompt version*: a frozen set of instructions for the AI; changing it means a new version, so results stay comparable.

### 2026-11-08 (evening) — Why the practice runs use a different database

**The question.** The benchmark needs a clean database, but the 16 practice runs (the "pilot") also write incidents, proposals and approvals. Where should they go?

**The problem with reusing one database.** The plan said "clear the benchmark database before the real runs". But the database has a rule: the audit log can only be added to, never changed or emptied, and the backend is not allowed to delete rows at all. That rule is a safety feature (nobody can hide what happened). It also means a database that has been used cannot be cleaned. And the safety check at the end of the benchmark reads every proposal in the database, so old practice proposals made by older, buggier code would be counted as if they were real.

**The decision.** Practice on the development database. Start the benchmark database fresh at the freeze, check it is empty, and use it only for the 48 counted runs.

**Who pays for the AI.** Usman pays for every call to the AI service with his own key. In sessions on Tanzeel's laptop he types the key in himself and removes it at the end of the project.

**Words.**
- *Append-only*: new rows can be added, existing rows can never be changed or deleted.
- *Pilot*: practice runs that are not counted.
- *Readiness check*: looking at a system without changing anything, to see that it is ready.

### 2026-11-08 (night) — The gate exam for the AI, and the ghosts of earlier runs

**What Gate G2 is.** The project's checkpoint for "does the AI work?": break the shop in all 8 ways, once each, and the AI must suggest the right fix (or, for the two faults no action can fix, hand the problem to a person) in at least 6 of 8. It passed with 7 of 8. Each suggested fix was approved through the system and really repaired the shop within about two and a half minutes.

**Ghosts of earlier runs.** Most of today's failures were not the AI being wrong, but the AI seeing traces of the *previous* test: its error messages still in the logs, a container that had crashed in the previous scenario and was still lying there. Each time the AI reasoned sensibly from misleading clues. The fix (ADR-27): after the test shop is reset, the AI's tools only look at what happened since the reset.

**Telling the AI the rules it is judged by.** Twice the AI was right but was marked down: once for not citing the deploy history, once for guessing a parameter name. The fix was to show it, right where it decides, which source each clue came from, the exact parameter names, and which releases can actually be rolled back to.

**Why it is slow.** From alarm to suggestion takes about 80 seconds; the AI itself thinks for only about 27 of them. Most of the rest is waiting for the database, which sits in Sydney: every question to it takes two thirds of a second, and each step asks several. A database closer to Pakistan would fix most of it.

**One honest disagreement.** For the memory leak, the AI says "a restart would only hide the leak, a person should look", while the benchmark expects a restart. That is a choice for the team, not a bug.

**Words.**
- *Gate*: a checkpoint with fixed pass criteria before the project moves on.
- *Round trip*: one question to a server and its answer; its time adds up when there are many.
- *Pinning a model*: fixing the exact AI model version, so all measurements use the same one.

### 2026-11-11 — Asking the far-away database fewer questions

**The problem.** The database is in Sydney, so each question to it takes a third to two thirds of a second. One investigation asked it well over a hundred questions, so a good part of its 90-second time limit went on waiting.

**Two cuts, nothing removed.** First, every time the program finished with a database connection, the library sent one extra "clean up this connection" message, even though the program never leaves anything behind to clean. That message is gone. Second, saving one piece of evidence used seven separate questions; now it uses four. The step that stops two writers from picking the same evidence number stays exactly as it was, and a new test proves it: with that step removed, the test fails every time.

**Why it matters.** About 40 fewer questions per investigation, roughly 12 to 25 seconds saved, so fewer runs should hit the time limit. Moving the database closer (Mumbai) would still help more.

**Words.**
- *Connection pool*: a few open connections to the database that the program reuses instead of opening a new one each time.
- *Mutation check*: breaking the code on purpose to see that a test notices.
