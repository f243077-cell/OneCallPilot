# ADR-27 — The agent's tools do not look back past a reset

- **Status:** Accepted, 2026-11-08 (Usman); for Tanzeel's review before the pilot (P4.1)
- **Changes:** architecture §6.3 (the `window` of `query_logs` and `query_metrics`). AI-005 still holds: the window stays inside the incident window ± 30 min, it only gets a later lower bound.
- **Code:** `backend/oncallpilot/agent/tools/window.py` (`RESET_SETTLE`, `clamp(not_before=…)`), `toolbox.py` (`_settled_reset`); tests in `backend/tests/agent/test_query_tools.py` and `test_summaries_toolbox.py`

## Context
`query_logs` and `query_metrics` read the incident window, which starts 10 minutes before the first anomaly (FR-007), and they may be asked to look back up to 30 minutes before it. `ocp smoke` and `bench run` start every run with `chaos reset` and inject 240 s later. So **every** default query reaches about 6 minutes back past that run's reset, and an explicit earlier `start` reaches further. Whatever ran before the reset (the previous run's fault, still active if it escalated or failed) is then evidence in the current run.

Found three ways, 2026-11-07 and 2026-11-08:
- `ocp smoke` run 11 (`docs/checks/smoke.md`): the disproof query found the error of the previous run's fault 5 minutes before this run's deploy; the model read it as a contradiction and dropped the right hypothesis.
- Recording the real model on the B2.6 tool recordings (`tests/agent_regression/record_model.py`): the recordings were made about 6 minutes apart, and scenario 7's logs held scenario 3's `DatabasePoolTimeout` from before its reset; scenario 1's held a queue error from before its reset. The model diagnosed the residue.
- `bench run` waits 4 minutes between runs (`benchmark/PROCEDURE.md`), so the counted benchmark would hit the same.

## Decision
The lower bound of every `query_logs` and `query_metrics` window is also never earlier than the last `kind=reset` record in the deploy ledger (at or before now) **plus 150 s**. `chaos reset` writes its record before it restores the testbed (up to 90 s, TB-007), and 1-minute query windows still show the old fault for 60 s after it ends: the same settle period as the detector's `warmup_settle_seconds` (ADR-24). A ledger without resets (any real deployment) changes nothing. `get_recent_deploys` is unchanged: the reset records themselves stay visible to the model.

**Amended 2026-11-08 (container state).** The same floor applies to `get_service_health`: while another container of the service runs, a stopped container whose Docker `FinishedAt` is before the floor is left out. Found in the Gate G2 run: scenario 6's crashed worker 2.2.0 slot (exit code 1) stays exited through every later reset, and in the next scenario 1 the model diagnosed it as a bad deploy. A crash after the floor is always shown. (`backend/oncallpilot/agent/tools/get_service_health.py`; also, the same file leaves out spare slots that were never started or were stopped cleanly, `7dcf469`.)

## Consequences
- A run's evidence only holds what happened after its own reset settled. With the 240 s warm-up, that leaves 90 s of healthy baseline before an injection for metric baselines.
- A query that lies wholly before the floor is refused as an empty window (`WindowError`) and still counts against the budget, like any other refused call.
- Benchmark runs keep their 4-minute spacing (no 30-minute gaps needed).
- Agent behaviour changes, before the `bench-freeze` tag. The real-model replay fixtures (`tests/agent_regression/scenarios/recorded_model/`) are recorded with this rule.
