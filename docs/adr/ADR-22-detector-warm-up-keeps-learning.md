# ADR-22 — Detector warm-up keeps learning normal samples (no relearning from scratch)

- **Status:** Accepted, 2026-10-21 (cloud session for Tanzeel; please review)
- **Changes:** architecture §6.8 (warm-up), FR-003's wording ("re-learn baselines")
- **Code:** `backend/oncallpilot/detector/engine.py`; tests in `backend/tests/test_detector_engine.py`

## Context
FR-003 says the detector must not open incidents for 180 s after a `kind=reset` ledger record and "MUST re-learn baselines during that window". Its acceptance test is: `chaos reset` followed by **immediate** fault injection opens no incident before t = 180 s, and opens one within 60 s after the warm-up ends.

If the baselines were relearnt from scratch during warm-up, the injected fault would be the only data the EWMA ever sees, would become the new normal, and would never trigger: the acceptance test could not pass.

## Decision
During warm-up nothing triggers, but the EWMA baselines are kept and keep learning from samples that do not breach the rules. Breaching samples are frozen out, exactly as outside warm-up (FR-002's freeze rule). So the baseline follows normal drift (for example a level shift that stays under the absolute floors), while a fault injected right after the reset is still anomalous when the warm-up ends and fires on the next breaching sample. Exception types seen during warm-up become the known types for `stack_traces`.

## Consequences
- FR-003's acceptance passes (`test_fault_right_after_reset_fires_only_after_warm_up`).
- A large, sudden change of the normal level at a reset (one that breaches the rules) is treated as an anomaly until it recovers. That is the intended behaviour for a testbed whose baseline is restored by `chaos reset`.
- On the very first start there is no baseline; the first samples build it, as before.
