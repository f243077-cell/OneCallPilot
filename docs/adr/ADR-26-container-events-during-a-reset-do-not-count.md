# ADR-26 — Container events during a reset do not count

- **Status:** Accepted, 2026-11-04 (Tanzeel)
- **Changes:** the `restarts` signal (FR-002) as the detector applies it after a reset; architecture §6.8
- **Code:** `backend/oncallpilot/detector/sources.py` (`LiveSampleSource.reset`, `RestartHistory.restarts`), `backend/oncallpilot/detector/loop.py`; tests in `backend/tests/test_detector_sources.py` and `backend/tests/test_detector_loop.py`

## Context
The `restarts` signal fires for a service when its running containers restarted at least twice in the last `window_seconds` (300 s), or when one of its containers exited with a non-zero code in that window. After a `chaos reset` the warm-up blocks triggers for `warmup_seconds` (180 s). The window is longer than the warm-up, so from 180 s to 300 s after the reset the signal still saw what happened before and during the reset.

Found on 2026-11-04, the first run after Docker Desktop started. The scenario 6 slot `cs-worker-220` (release 2.2.0, which refuses to start) was crash-looping. `ocp up`'s reset stopped it at 17:29:35, and its last exit code was 1. At 17:32:37, 185 s after the reset and 5 s after the warm-up ended, the detector opened a worker `restarts` incident although no fault was injected. The same happens after every scenario 1 run (memory-leak restarts) or scenario 6 run (the crash loop): in the benchmark, the next run would get a false worker incident.

## Decision
The detector loop tells the sample source about a reset as well as the engine. From the reset until the end of the settle period (`warmup_settle_seconds`, 150 s, ADR-24):
- restarts no longer count: the restart window starts at the end of the settle period, not before;
- a container that exited non-zero before the end of the settle period is not flagged.

Restarts and non-zero exits after the settle period count as before. `ocp smoke`, `bench run` and the B2.6 recorder inject 240 s after the reset, so a real fault is always after the settle.

## Consequences
- A crash that the reset stopped raises no incident (`test_a_crash_before_the_reset_settled_does_not_count_after_the_warm_up`); a crash after the settle still does (`test_a_crash_after_the_reset_settled_still_counts`).
- A fault that keeps crashing a container through the reset is seen again by the first restart after the settle.
- `backend/config/detector.yaml` does not change, so `detector_config_version` (ADR-20) does not change. This is before the `bench-freeze` tag, so no benchmark incident spans the change.
