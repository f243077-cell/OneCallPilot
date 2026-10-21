"""Signal verification bounds (FR-014): stricter than the detector, never normal without data."""

from __future__ import annotations

from oncallpilot.detector.config import load_config, read_config
from oncallpilot.detector.engine import RestartsSample, Samples, TracesSample
from oncallpilot.detector.verify import signal_normal
from oncallpilot.worker.loops import triggering_signals
from tests.test_detector_config import CONFIG

RULES = load_config(read_config(CONFIG))


def test_statistical_signals_use_the_absolute_floors() -> None:
    assert signal_normal(RULES, "error_rate", Samples(error_rate=0.01), 0.004)
    assert not signal_normal(RULES, "error_rate", Samples(error_rate=0.03), 0.004)
    # p95: below max(0.3 s, 2 x baseline).
    assert signal_normal(RULES, "p95_latency", Samples(p95_latency=0.29), 0.1)
    assert not signal_normal(RULES, "p95_latency", Samples(p95_latency=0.5), 0.2)
    assert signal_normal(RULES, "job_failure_rate", Samples(job_failure_rate=0.0), None)


def test_no_data_is_never_normal() -> None:
    assert not signal_normal(RULES, "error_rate", None, 0.004)
    assert not signal_normal(RULES, "error_rate", Samples(), 0.004)
    assert not signal_normal(RULES, "restarts", Samples(), None)
    assert not signal_normal(RULES, "stack_traces", Samples(), None)
    assert not signal_normal(RULES, "unknown_signal", Samples(error_rate=0.0), None)


def test_restarts_and_traces() -> None:
    def restarts(count: int, running: int, exited: bool = False) -> Samples:
        return Samples(restarts=RestartsSample(count, exited, running))

    # An old slot's non-zero exit does not matter once a container runs again.
    assert signal_normal(RULES, "restarts", restarts(1, 1, exited=True), None)
    assert not signal_normal(RULES, "restarts", restarts(2, 1), None)
    assert not signal_normal(RULES, "restarts", restarts(0, 0), None)
    assert signal_normal(RULES, "stack_traces", Samples(stack_traces=TracesSample(4)), None)
    assert not signal_normal(RULES, "stack_traces", Samples(stack_traces=TracesSample(5)), None)


def test_triggering_signals_include_updates_and_keep_the_first_baseline() -> None:
    trigger = {
        "signals": [{"name": "error_rate", "baseline": 0.004}],
        "updates": [{"signals": [{"name": "error_rate", "baseline": 0.2}, {"name": "restarts"}]}],
    }
    assert triggering_signals(trigger) == {"error_rate": 0.004, "restarts": None}
