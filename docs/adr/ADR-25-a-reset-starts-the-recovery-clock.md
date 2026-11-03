# ADR-25 — A reset starts the recovery clock

- **Status:** Accepted, 2026-11-03 (Tanzeel)
- **Changes:** FR-011 recovery as the detector applies it after a reset; architecture §6.8 (recovery)
- **Code:** `backend/oncallpilot/detector/engine.py` (`Detector.reset`); tests in `backend/tests/test_detector_engine.py`

## Context
A signal that triggers stays triggered until it has been normal for `recovery_minutes` (3 min, FR-011). While it is triggered it fires no new alert, so one fault raises one incident.

A reset did not touch that state. During the settle period (the first 150 s, ADR-24), statistical and stack-trace samples are skipped, so the recovery clock could not start before then. The earliest recovery after a reset was therefore 150 + 180 = 330 s. `ocp smoke`, `bench run` and the B2.6 recorder inject 240 s after the reset.

Found on 2026-11-03 by the B2.6 recorder: scenario 3 (db-pool, api `p95_latency`) was followed by scenario 5 (traffic-spike, api `p95_latency`). api p95 stayed near 9.7 s for 10 minutes and no incident opened, because the signal was still triggered from scenario 3. The benchmark runs scenarios back to back, so it would hit the same problem whenever two runs share a signal and service, including two runs of the same scenario.

## Decision
On a reset, every triggered signal counts as normal from the reset time:
- the breaches seen during the settle period are not observed (as for learning, ADR-24);
- a breach after the settle period stops the clock as usual, so a fault that outlasts the reset stays open;
- otherwise the service is reported recovered `recovery_minutes` after the reset (180 s, inside the warm-up), and the next fault alerts again.

## Consequences
- A run that follows a run on the same signal and service raises its incident (`test_a_fault_that_triggered_before_a_reset_fires_again_after_it`). A fault that never ends is not recovered by the reset (`test_a_fault_that_outlasts_the_settle_is_not_recovered_by_the_reset`).
- The recovery is reported as usual: the auto-resolver closes the previous run's incident if it is still open (`auto_recovered`).
- The settings file does not change, so `detector_config_version` (ADR-20) does not change. This is before the `bench-freeze` tag, so no benchmark incident spans the change.
