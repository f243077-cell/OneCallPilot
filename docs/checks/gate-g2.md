# Gate G2 — "every fault gets a diagnosis and proposal" (end of Week 4)

> Checklist for `phases.md` Phase 2, Gate G2. Status as of **2026-10-10**. Evidence so far comes from branch `tanzeel`; nothing is merged into `main` yet.
> Owners: **T** = Tanzeel (Stream A), **U** = Usman (Stream B), **T+U** = shared.

| # | Criterion | Status | Evidence we have | Missing, and who owns it |
|---|---|---|---|---|
| 1 | With the real LLM, each scenario produces a proposal matching `scenarios.yaml`, or an escalation for 7 and 8, in at least 6 of 8 single runs | **Not started** | The 8 faults are reproducible and persist (Gate G1 criteria 2 and 3). | **U:** agent, tools, prompts (B2.1–B2.8). **T:** inject during the runs. |
| 2 | Approving through `ocp smoke` executes the action; the two-stage health check resolves scenarios 1–6 | **Not started** | Runner verification is done (A2.1). | **T:** dry runs (A2.3), execute handlers (A2.4), health check (A2.5). **U:** approve endpoints, result consumer, verifier (B2.9, B2.10), `ocp smoke`. |
| 3 | TEST-003, TEST-004, TEST-005 and TEST-015 are green | **TEST-015 green (rw half)**; others not yet | **TEST-015 (socket-proxy-rw), 54/54 passed** on 2026-10-10 against the real proxy, and in CI (`runner.yml`, job `runner-proxy`). Allowed: container reads, start, stop, restart, kill. Refused with 403: create (incl. privileged), exec, delete, image pull and list, network and volume calls, every other container POST, every endpoint beyond containers, logs/archive/export/top/changes, libpod, path tricks. The test container has no Docker socket. Details and one known limitation (`attach/ws` returns 101) in `docs/checks/proxy.md`. | **TEST-015 ro half (U):** any POST through `socket-proxy-ro` → 403, once it is in `observability.yml`. **TEST-004 (T, A2.6):** forbidden-action suite with zero Docker write calls; A2.1 already refuses every case listed (`week-3.md`). **TEST-003, TEST-005 (U).** |
| 4 | Agent regression suite green in CI; `agent_meta` complete | **Not started** | — | **U** |
| 5 | LLM model pinned | **Not started** | — | **U** (ADR-13, by end of Week 4) |
