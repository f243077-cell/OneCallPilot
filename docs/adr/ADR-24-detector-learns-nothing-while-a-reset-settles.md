# ADR-24 — The detector learns nothing while a reset settles

- **Status:** Accepted, 2026-11-02 (Tanzeel)
- **Changes:** ADR-22 (what the warm-up learns), architecture §6.8 (warm-up), `backend/config/detector.yaml` (`warmup_settle_seconds`)
- **Code:** `backend/oncallpilot/detector/engine.py`, `config.py`; tests in `backend/tests/test_detector_engine.py`

## Context
ADR-22 lets the detector keep learning during the 180 s warm-up after a `kind=reset` ledger record: normal samples update the baselines, and every exception type seen becomes a known one.

Two facts make the first part of that warm-up unrepresentative:
- `chaos reset` writes its ledger records **before** it restores the containers (`d444e80`, so the reset churn never raises an alert). For 18–43 s (up to 90 s, TB-007) the previous fault is still running.
- The detector's queries look back one minute (`rate(…[1m])`, Loki `count_over_time(…[1m])`), so the old fault stays visible for up to 60 s after it ends.

Found on 2026-11-02 with `ocp smoke`: after a run that left scenario 2 active, the next reset's warm-up learnt `IndexError` as a known exception type and, on a fresh worker, an 11 % error rate as the baseline. The same fault injected after the warm-up raised no incident. A fresh detector fed the same live samples alerted at once. The benchmark starts every run with `chaos reset`, often with the previous run's fault still active (escalated scenarios 7 and 8, failed runs), so it would hit this too.

## Decision
For the first `warmup_settle_seconds` (150 s: the reset itself, up to 90 s, plus the 60 s query window) after a reset or a start, the detector learns nothing: no baseline updates and no known exception types, and, as in the whole warm-up, nothing triggers. It learns during the rest of the warm-up (the last 30 s) and afterwards as before. The value must be shorter than `warmup_seconds`.

## Consequences
- A fault still running when a reset starts is no longer learnt as normal (`test_a_fault_still_running_when_the_reset_starts_is_not_learnt`).
- FR-003 still holds: a fault injected right after the reset is breaching against the baseline from before the reset and fires once the warm-up ends.
- A fresh worker builds its first baseline from fewer warm-up samples (6 instead of 36); `ocp smoke` and `bench run` wait 240 s after the reset, so 18 samples of healthy traffic precede an injection.
- `detector_config_version` changes (ADR-20), so incidents opened before and after this change are told apart. This is before the `bench-freeze` tag.
