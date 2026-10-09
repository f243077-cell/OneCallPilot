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
