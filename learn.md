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
