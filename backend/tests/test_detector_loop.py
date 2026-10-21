"""The detector loop with fake sources (FR-001, FR-003) and the ledger reader."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from oncallpilot_contracts.api import AlertIn
from pydantic import SecretStr

from oncallpilot.detector.config import load_config, read_config
from oncallpilot.detector.engine import Detector, Samples
from oncallpilot.detector.loop import DetectorLoop, HttpAlertSink, MonitorSettingsView
from oncallpilot.ledger import latest_reset, read_records
from tests.test_detector_config import CONFIG

T0 = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
LEDGER = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "ledger"


class Source:
    def __init__(self) -> None:
        self.error_rate = 0.004
        self.fail = False
        self.calls = 0

    async def collect(self, at: datetime) -> dict[str, Samples]:
        self.calls += 1
        if self.fail:
            raise TimeoutError("prometheus")
        return {"api": Samples(error_rate=self.error_rate)}


class Sink:
    def __init__(self) -> None:
        self.sent: list[AlertIn] = []
        self.down = False

    async def send(self, alert: AlertIn) -> None:
        if self.down:
            raise httpx.ConnectError("backend-api down")
        self.sent.append(alert)


class Resets:
    def __init__(self) -> None:
        self.latest: tuple[uuid.UUID, datetime] | None = None

    async def __call__(self) -> tuple[uuid.UUID, datetime] | None:
        return self.latest


def make() -> tuple[DetectorLoop, Source, Sink, Resets]:
    config = load_config(read_config(CONFIG))
    detector = Detector(config, started_at=T0 - timedelta(seconds=config.warmup_seconds))
    source, sink, resets = Source(), Sink(), Resets()
    return DetectorLoop(detector, source, sink, resets), source, sink, resets


async def run_for(loop: DetectorLoop, start: datetime, cycles: int) -> datetime:
    at = start
    for _ in range(cycles):
        await loop.cycle(at)
        at += timedelta(seconds=5)
    return at


async def test_a_fault_sends_one_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    loop, source, sink, _ = make()
    at = await run_for(loop, T0, 60)
    source.error_rate = 0.31
    await run_for(loop, at, 10)
    [alert] = sink.sent
    assert alert.service == "api" and alert.source == "detector"
    assert alert.signals[0].name == "error_rate"


async def test_source_failures_are_logged_and_the_loop_goes_on() -> None:
    loop, source, sink, _ = make()
    at = await run_for(loop, T0, 30)
    source.fail = True
    at = await run_for(loop, at, 5)
    source.fail = False
    source.error_rate = 0.31
    await run_for(loop, at, 5)
    assert len(sink.sent) == 1


async def test_undelivered_alerts_are_retried_each_cycle() -> None:
    loop, source, sink, _ = make()
    at = await run_for(loop, T0, 60)
    sink.down = True
    source.error_rate = 0.31
    at = await run_for(loop, at, 6)
    assert sink.sent == [] and loop.undelivered == 1
    sink.down = False
    await run_for(loop, at, 1)
    assert len(sink.sent) == 1 and loop.undelivered == 0


async def test_a_new_reset_record_starts_the_warm_up() -> None:
    loop, source, sink, resets = make()
    resets.latest = (uuid.uuid4(), T0 - timedelta(hours=1))  # there at start: no warm-up
    at = await run_for(loop, T0, 60)
    assert not loop.detector.warming_up(at)
    resets.latest = (uuid.uuid4(), at)
    at = await run_for(loop, at, 1)
    assert loop.detector.warming_up(at)
    source.error_rate = 0.31
    at = await run_for(loop, at, 30)  # 150 s of the 180 s warm-up
    assert sink.sent == []
    await run_for(loop, at, 12)
    assert len(sink.sent) == 1


async def test_run_paces_cycles_by_the_interval() -> None:
    loop, source, _, _ = make()
    clock = [T0]
    sleeps: list[float] = []

    def now() -> datetime:
        return clock[0]

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += timedelta(seconds=seconds)

    await loop.run(5.0, now, sleep, cycles=4)
    assert source.calls == 4
    assert sleeps == [5.0] * 4


async def test_http_sink_posts_with_the_ingest_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"incident_id": str(uuid.uuid4()), "deduplicated": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sink = HttpAlertSink(client, "http://backend-api:8000/ingest/alert", SecretStr("tok"))
        alert = AlertIn.model_validate(
            {
                "source": "detector",
                "service": "api",
                "signals": [
                    {
                        "name": "error_rate",
                        "value": 0.3,
                        "baseline": 0.004,
                        "zscore": 9.0,
                        "first_anomalous_at": "2026-10-21T10:00:00Z",
                    }
                ],
                "observed_at": "2026-10-21T10:00:15Z",
            }
        )
        await sink.send(alert)
    [request] = seen
    assert request.headers["x-ingest-token"] == "tok"
    assert AlertIn.model_validate_json(request.content) == alert


def test_ledger_reader_skips_a_partial_last_line(tmp_path: Path) -> None:
    [fixture] = sorted(LEDGER.glob("*.jsonl"))
    lines = fixture.read_text().splitlines(keepends=True)
    path = tmp_path / "deploys.jsonl"
    path.write_text("".join(lines) + lines[0][:40])
    records = read_records(path)
    assert len(records) == len(lines)
    assert read_records(tmp_path / "missing.jsonl") == []
    reset = latest_reset(records)
    assert reset is None or reset.kind == "reset"


def make_with(settings: object) -> tuple[DetectorLoop, Source, Sink]:
    loop, source, sink, _ = make()
    loop.settings = settings  # type: ignore[assignment]
    loop.base_config = read_config(CONFIG)
    return loop, source, sink


async def test_monitor_settings_change_thresholds_and_services() -> None:
    views: list[MonitorSettingsView | None] = [None]

    async def settings() -> MonitorSettingsView | None:
        return views[-1]

    loop, source, sink = make_with(settings)
    at = await run_for(loop, T0, 2)
    assert loop.detector.config.signals.error_rate.z == 4

    views.append(MonitorSettingsView(2, frozenset({"worker"}), {"error_rate.z": 6.0}))
    at = await run_for(loop, at, 60)
    assert loop.detector.config.signals.error_rate.z == 6.0
    # api is no longer monitored: a burst of errors there raises nothing.
    source.error_rate = 0.31
    at = await run_for(loop, at, 10)
    assert sink.sent == []

    # Monitored again: the same fault alerts.
    views.append(MonitorSettingsView(3, None, {}))
    source.error_rate = 0.004
    at = await run_for(loop, at, 60)
    source.error_rate = 0.31
    at = await run_for(loop, at, 10)
    assert [a.service for a in sink.sent] == ["api"]

    # An invalid version is ignored; the last good rules stay.
    views.append(MonitorSettingsView(4, None, {"no_such.rule": 1.0}))
    await run_for(loop, at, 1)
    assert loop.detector.config.signals.error_rate.z == 4


async def test_unreadable_settings_keep_the_rules() -> None:
    async def broken() -> MonitorSettingsView | None:
        raise OSError("database down")

    loop, _, _ = make_with(broken)
    await run_for(loop, T0, 1)
    assert loop.detector.config.signals.error_rate.z == 4
