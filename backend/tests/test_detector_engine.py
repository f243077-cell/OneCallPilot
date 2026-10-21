"""Detector rules on synthetic series (FR-002, FR-003, FR-011; architecture §6.8)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from oncallpilot.detector.config import DetectorConfig, load_config, read_config
from oncallpilot.detector.engine import (
    Detector,
    Ewma,
    RestartsSample,
    Samples,
    TracesSample,
)
from tests.test_detector_config import CONFIG

T0 = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
STEP = timedelta(seconds=5)


@pytest.fixture
def config() -> DetectorConfig:
    return load_config(read_config(CONFIG))


def warm_detector(config: DetectorConfig) -> tuple[Detector, datetime]:
    """A detector whose warm-up is over at the returned time."""
    detector = Detector(config, started_at=T0)
    return detector, T0 + timedelta(seconds=config.warmup_seconds)


def noisy(rng: random.Random, mean: float, sd: float) -> float:
    """Gaussian noise clipped to ±3σ."""
    return mean + max(-3 * sd, min(3 * sd, rng.gauss(0, sd)))


def feed_baseline(
    detector: Detector, at: datetime, n: int, rng: random.Random, **means: tuple[float, float]
) -> datetime:
    for _ in range(n):
        values = {name: max(0.0, noisy(rng, m, sd)) for name, (m, sd) in means.items()}
        cycle = detector.step(at, {"api": Samples(**values)})  # type: ignore[arg-type]
        assert cycle.alerts == []
        at += STEP
    return at


# --- FR-002 -------------------------------------------------------------------


def test_step_to_31_percent_errors_triggers_within_3_samples(config: DetectorConfig) -> None:
    rng = random.Random(1)
    detector, at = warm_detector(config)
    at = feed_baseline(detector, at, 120, rng, error_rate=(0.004, 0.001))
    first_bad = at
    results = []
    for _ in range(3):
        results.append(detector.step(at, {"api": Samples(error_rate=0.31)}).alerts)
        at += STEP
    assert [bool(a) for a in results] == [False, False, True]  # the third breaching sample
    alerts = results[-1]
    at -= STEP
    [alert] = alerts
    [signal] = alert.signals
    assert alert.service == "api"
    assert (signal.name, signal.value) == ("error_rate", 0.31)
    assert signal.first_anomalous_at == first_bad
    assert signal.baseline is not None and signal.baseline < 0.01
    # It fires once, not on every later sample.
    assert detector.step(at + STEP, {"api": Samples(error_rate=0.31)}).alerts == []


def test_noise_within_3_sigma_never_triggers_over_10000_samples(config: DetectorConfig) -> None:
    rng = random.Random(42)
    detector, at = warm_detector(config)
    # Well above the 2 % floor, so only the z-score rule stands between noise and an alert.
    feed_baseline(detector, at, 10_000, rng, error_rate=(0.10, 0.01))


def test_one_and_a_half_percent_errors_stay_under_the_floor(config: DetectorConfig) -> None:
    rng = random.Random(3)
    detector, at = warm_detector(config)
    at = feed_baseline(detector, at, 120, rng, error_rate=(0.001, 0.0002))
    for _ in range(50):
        assert detector.step(at, {"api": Samples(error_rate=0.015)}).alerts == []
        at += STEP


def test_baseline_is_frozen_while_anomalous(config: DetectorConfig) -> None:
    rng = random.Random(5)
    detector, at = warm_detector(config)
    at = feed_baseline(detector, at, 60, rng, error_rate=(0.004, 0.001))
    state = detector._services["api"].signals["error_rate"]
    mean, var = state.ewma.mean, state.ewma.var
    for _ in range(40):
        detector.step(at, {"api": Samples(error_rate=0.31)})
        at += STEP
    assert (state.ewma.mean, state.ewma.var) == (mean, var)


def test_p95_needs_twice_the_baseline_and_300_ms(config: DetectorConfig) -> None:
    rng = random.Random(7)
    detector, at = warm_detector(config)
    at = feed_baseline(detector, at, 120, rng, p95_latency=(0.05, 0.005))
    # 0.25 s: far above in z, but under the 300 ms floor.
    for _ in range(5):
        assert detector.step(at, {"api": Samples(p95_latency=0.25)}).alerts == []
        at += STEP
    for _ in range(3):
        alerts = detector.step(at, {"api": Samples(p95_latency=1.2)}).alerts
        at += STEP
    assert [s.name for s in alerts[0].signals] == ["p95_latency"]


def test_job_failure_rate_is_watched_on_the_worker_only(config: DetectorConfig) -> None:
    detector, at = warm_detector(config)
    rng = random.Random(9)
    for _ in range(60):
        detector.step(at, {"worker": Samples(job_failure_rate=max(0.0, noisy(rng, 0.01, 0.002)))})
        detector.step(at, {"api": Samples(job_failure_rate=0.0)})
        at += STEP
    fired = []
    for _ in range(3):
        fired += detector.step(
            at, {"worker": Samples(job_failure_rate=0.4), "api": Samples(job_failure_rate=0.9)}
        ).alerts
        at += STEP
    assert [a.service for a in fired] == ["worker"]


# --- restarts and stack traces --------------------------------------------------


@pytest.mark.parametrize(
    ("sample", "fires"),
    [
        (RestartsSample(restarts_in_window=1, exited_nonzero=False, running=1), False),
        (RestartsSample(restarts_in_window=2, exited_nonzero=False, running=1), True),
        (RestartsSample(restarts_in_window=0, exited_nonzero=True, running=1), True),
        (RestartsSample(restarts_in_window=0, exited_nonzero=False, running=0), True),
        (
            RestartsSample(
                restarts_in_window=0, exited_nonzero=False, running=0, expected_running=False
            ),
            False,
        ),
    ],
)
def test_restarts_rule(config: DetectorConfig, sample: RestartsSample, fires: bool) -> None:
    detector, at = warm_detector(config)
    alerts = detector.step(at, {"worker": Samples(restarts=sample)}).alerts
    assert bool(alerts) is fires


def test_stack_traces_need_a_new_exception_type(config: DetectorConfig) -> None:
    detector = Detector(config, started_at=T0)
    warm = TracesSample(per_minute=9, exception_types=frozenset({"TimeoutError"}))
    assert detector.step(T0, {"api": Samples(stack_traces=warm)}).alerts == []
    after = T0 + timedelta(seconds=config.warmup_seconds)
    assert detector.step(after, {"api": Samples(stack_traces=warm)}).alerts == []
    few = TracesSample(per_minute=4, exception_types=frozenset({"IndexError"}))
    assert detector.step(after, {"api": Samples(stack_traces=few)}).alerts == []
    new = TracesSample(per_minute=12, exception_types=frozenset({"IndexError", "TimeoutError"}))
    [alert] = detector.step(after, {"api": Samples(stack_traces=new)}).alerts
    assert alert.signals[0].name == "stack_traces"


# --- FR-003 warm-up -------------------------------------------------------------


def test_fault_right_after_reset_fires_only_after_warm_up(config: DetectorConfig) -> None:
    rng = random.Random(11)
    detector, at = warm_detector(config)
    at = feed_baseline(detector, at, 60, rng, error_rate=(0.004, 0.001))
    reset_at = at
    detector.reset(reset_at)
    warm_end = reset_at + timedelta(seconds=config.warmup_seconds)
    fired_at = None
    for _ in range(100):  # the fault is injected right after the reset and stays
        cycle = detector.step(at, {"api": Samples(error_rate=max(0.0, noisy(rng, 0.31, 0.01)))})
        if cycle.alerts:
            fired_at = at
            break
        at += STEP
    assert fired_at is not None
    assert warm_end <= fired_at <= warm_end + timedelta(seconds=60)


def test_warm_up_keeps_learning_normal_samples(config: DetectorConfig) -> None:
    rng = random.Random(13)
    detector, at = warm_detector(config)
    at = feed_baseline(detector, at, 60, rng, error_rate=(0.004, 0.001))
    detector.reset(at)
    assert detector.warming_up(at)
    # The normal level moves during warm-up (0.4 % -> 1 %, under the 2 % floor, so
    # not a breach); the baseline follows it.
    end = at + timedelta(seconds=config.warmup_seconds)
    while at < end:
        value = max(0.0, noisy(rng, 0.01, 0.001))
        assert detector.step(at, {"api": Samples(error_rate=value)}).alerts == []
        at += STEP
    mean = detector._services["api"].signals["error_rate"].ewma.mean
    assert mean is not None and 0.008 < mean < 0.012
    # The new level is normal now.
    for _ in range(20):
        value = max(0.0, noisy(rng, 0.01, 0.001))
        assert detector.step(at, {"api": Samples(error_rate=value)}).alerts == []
        at += STEP
    assert not detector.warming_up(at)
    fired = []
    for _ in range(12):  # within 60 s after warm-up
        fired += detector.step(at, {"api": Samples(error_rate=0.31)}).alerts
        at += STEP
    assert len(fired) == 1


# --- FR-011 recovery ------------------------------------------------------------


def test_recovery_after_three_normal_minutes(config: DetectorConfig) -> None:
    rng = random.Random(17)
    detector, at = warm_detector(config)
    at = feed_baseline(detector, at, 60, rng, error_rate=(0.004, 0.001))
    for _ in range(3):
        detector.step(at, {"api": Samples(error_rate=0.31)})
        at += STEP
    normal_from = at
    recovered_at = None
    for _ in range(100):
        cycle = detector.step(at, {"api": Samples(error_rate=max(0.0, noisy(rng, 0.004, 0.001)))})
        assert cycle.alerts == []
        if cycle.recovered:
            recovered_at = at
            break
        at += STEP
    assert recovered_at == normal_from + timedelta(minutes=3)
    # Reported once; a new fault then triggers again.
    assert detector.step(at + STEP, {"api": Samples(error_rate=0.004)}).recovered == []


def test_a_relapse_restarts_the_recovery_clock(config: DetectorConfig) -> None:
    rng = random.Random(19)
    detector, at = warm_detector(config)
    at = feed_baseline(detector, at, 60, rng, error_rate=(0.004, 0.001))
    for _ in range(3):
        detector.step(at, {"api": Samples(error_rate=0.31)})
        at += STEP
    for _ in range(24):  # two normal minutes
        assert detector.step(at, {"api": Samples(error_rate=0.004)}).recovered == []
        at += STEP
    detector.step(at, {"api": Samples(error_rate=0.31)})  # relapse
    at += STEP
    for _ in range(35):  # under three minutes again
        assert detector.step(at, {"api": Samples(error_rate=0.004)}).recovered == []
        at += STEP


def test_ewma_basics() -> None:
    ewma = Ewma(alpha=0.1)
    assert ewma.zscore(1.0) is None
    for value in (1.0, 1.0, 1.0):
        ewma.update(value)
    assert ewma.zscore(1.0) == 0.0
    assert ewma.zscore(2.0) == float("inf")
    ewma.update(2.0)
    z = ewma.zscore(1.5)
    assert z is not None and z > 0
