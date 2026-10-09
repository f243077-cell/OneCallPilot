"""chaos reset, status, inject and verify fixes against an in-memory Docker (TB-006..TB-009)."""

import re
from pathlib import Path
from typing import Any

import pytest

from chaos_cli import helper, ledger
from chaos_cli.config import (
    API_SLOTS,
    BASELINE_RUNNING,
    WORKER_SLOTS,
    Secrets,
    load_secrets,
    read_env_file,
)
from chaos_cli.testbed import SCENARIOS, ScenarioError, Testbed, metric_value, scenario_number
from tests.fakes import FakeDocker


@pytest.fixture
def docker() -> FakeDocker:
    return FakeDocker()


@pytest.fixture
def testbed(docker: FakeDocker) -> Testbed:
    return Testbed(docker, Secrets("t" * 32), ledger.Releases.load(), sleep=lambda _: None)  # type: ignore[arg-type]


def running(docker: FakeDocker) -> set[str]:
    return {n for n, s in docker.containers.items() if s.running}


def ledger_records(docker: FakeDocker) -> list[Any]:
    return ledger.parse_lines(docker.ledger)


# --- names ---


@pytest.mark.parametrize("value,number", [("2", 2), ("bad-deploy", 2), ("log-injection", 8)])
def test_scenario_names_and_numbers(value: str, number: int) -> None:
    assert scenario_number(value) == number


def test_unknown_scenario_is_refused() -> None:
    with pytest.raises(ScenarioError):
        scenario_number("9")


# --- reset (TB-007) ---


def test_reset_restores_the_baseline_and_reseeds_the_ledger(
    testbed: Testbed, docker: FakeDocker
) -> None:
    docker.start(["cs-api-150-1", "cs-worker-220"])
    docker.stop(["cs-redis", "cs-worker-210"])
    docker.ledger = "garbage that reset replaces\n"
    testbed.reset()
    assert running(docker) == set(BASELINE_RUNNING)
    assert ("restart", "cs-api-140-1") in docker.actions  # clears in-process switches
    records = ledger_records(docker)
    assert [r.kind for r in records[-2:]] == ["reset", "reset"]
    switched_off = {(s["url"], str(s["json"])) for s in docker.http_steps()}
    assert ("http://cs-payments:8002/internal/delay", "{'delay_ms': 0}") in switched_off
    assert ("http://cs-loadgen:8003/internal/mode", "{'mode': 'baseline'}") in switched_off
    assert ("http://cs-loadgen:8003/internal/coupon-stream", "{'enabled': False}") in switched_off


def test_reset_creates_missing_slots(testbed: Testbed, docker: FakeDocker) -> None:
    del docker.containers["cs-api-150-5"]
    testbed.reset()
    assert docker.created_slots


def test_reset_refuses_without_the_baseline_containers(
    testbed: Testbed, docker: FakeDocker
) -> None:
    del docker.containers["cs-lb"]
    with pytest.raises(ScenarioError, match="cs-lb"):
        testbed.reset()


def test_status_reports_baseline_only_after_reset(testbed: Testbed, docker: FakeDocker) -> None:
    docker.responses = {
        "http://cs-payments:8002/internal/delay": {"delay_ms": 0},
        "http://cs-loadgen:8003/internal/status": {"mode": "baseline", "coupon_stream": False},
    }
    before = testbed.snapshot()
    assert not before.baseline
    assert "the ledger does not end with the reset records" in before.problems
    testbed.reset()
    after = testbed.snapshot()
    assert after.baseline, after.problems
    docker.responses["http://cs-payments:8002/internal/delay"] = {"delay_ms": 2500}
    assert "payments delay ms = 2500" in testbed.snapshot().problems


# --- inject (TB-006) ---


def test_inject_1_enables_retention_on_the_running_worker(
    testbed: Testbed, docker: FakeDocker
) -> None:
    testbed.inject(1)
    (step,) = docker.http_steps()
    assert step["url"] == "http://cs-worker-210:8001/internal/chaos/leak"
    assert step["json"] == {"enabled": True} and step["auth"] is True


def test_inject_2_deploys_150_blue_green_and_records_ci(
    testbed: Testbed, docker: FakeDocker
) -> None:
    docker.start(["cs-api-140-2"])
    testbed.inject(2)
    assert running(docker) & set(API_SLOTS) == {"cs-api-150-1", "cs-api-150-2"}
    first_stop = docker.actions.index(("stop", "cs-api-140-1"))
    assert docker.actions.index(("start", "cs-api-150-2")) < first_stop  # new before old
    (record,) = ledger_records(docker)
    assert (record.kind, record.release, record.replicas, record.deployed_by) == (
        "deploy", "1.5.0", 2, "ci",
    )  # fmt: skip


def test_inject_3_arms_every_running_api_slot(testbed: Testbed, docker: FakeDocker) -> None:
    docker.start(["cs-api-140-2"])
    testbed.inject(3)
    urls = sorted(step["url"] for step in docker.http_steps())
    assert urls == [
        "http://cs-api-140-1:8000/internal/chaos/slow-queries",
        "http://cs-api-140-2:8000/internal/chaos/slow-queries",
    ]


def test_inject_4_stops_redis(testbed: Testbed, docker: FakeDocker) -> None:
    testbed.inject(4)
    assert docker.actions == [("stop", "cs-redis")]


def test_inject_5_6_7_8(testbed: Testbed, docker: FakeDocker) -> None:
    testbed.inject(5)
    testbed.inject(7)
    testbed.inject(8)
    bodies = {step["url"]: step["json"] for step in docker.http_steps()}
    assert bodies["http://cs-loadgen:8003/internal/mode"] == {"mode": "spike"}
    assert bodies["http://cs-payments:8002/internal/delay"] == {"delay_ms": 2500}
    assert bodies["http://cs-loadgen:8003/internal/coupon-stream"] == {"enabled": True}
    testbed.inject(6)
    assert running(docker) & set(WORKER_SLOTS) == {"cs-worker-220"}
    (record,) = ledger_records(docker)
    assert (record.service, record.release, record.deployed_by) == ("worker", "2.2.0", "ci")


def test_every_scenario_has_an_inject(testbed: Testbed) -> None:
    for number in SCENARIOS:
        assert testbed.inject(number)


def test_injecting_bad_deploy_twice_is_refused(testbed: Testbed) -> None:
    testbed.inject(2)
    with pytest.raises(ScenarioError, match="already runs"):
        testbed.inject(2)


# --- fixes applied by verify (TB-008) ---


def test_fixes_mirror_the_expected_actions(testbed: Testbed, docker: FakeDocker) -> None:
    testbed.inject(2)
    testbed.fix(2)
    assert running(docker) & set(API_SLOTS) == {"cs-api-140-1"}
    assert ledger_records(docker)[-1].kind == "rollback"
    testbed.fix(5)
    assert running(docker) & set(API_SLOTS) == {"cs-api-140-1", "cs-api-140-2", "cs-api-140-3"}
    assert (ledger_records(docker)[-1].kind, ledger_records(docker)[-1].replicas) == ("scale", 3)
    testbed.inject(6)
    testbed.fix(6)
    assert running(docker) & set(WORKER_SLOTS) == {"cs-worker-210"}
    docker.actions.clear()
    testbed.fix(4)
    assert docker.actions == [("restart", "cs-redis")]
    for number in (7, 8):
        with pytest.raises(ScenarioError, match="escalated"):
            testbed.fix(number)


# --- small parts ---


def test_metric_value_reads_labelled_samples() -> None:
    text = (
        'db_pool_connections{instance="x",state="idle"} 0.0\n'
        'db_pool_connections{instance="x",state="in_use"} 5.0\n'
        "process_resident_memory_bytes 4.7e+07\n"
    )
    assert metric_value(text, "db_pool_connections", state="in_use") == 5.0
    assert metric_value(text, "process_resident_memory_bytes") == 4.7e07
    assert metric_value(text, "missing") is None


def test_token_comes_from_the_env_file_and_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHAOS_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("# comment\nCHAOS_TOKEN=" + "k" * 48 + "\nOTHER=1\n", encoding="utf-8")
    assert read_env_file(env)["OTHER"] == "1"
    assert load_secrets(env).chaos_token == "k" * 48
    env.write_text("CHAOS_TOKEN=short\n", encoding="utf-8")
    with pytest.raises(Exception, match="CHAOS_TOKEN"):
        load_secrets(env)


def test_helper_ledger_append_and_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(helper, "LEDGER_DIR", str(tmp_path))
    monkeypatch.setattr(helper, "LEDGER_FILE", str(tmp_path / "deploys.jsonl"))
    monkeypatch.setattr(helper, "_chown", lambda *_: None)
    assert helper.run([{"op": "ledger_read"}], "") == [""]
    helper.run([{"op": "ledger_replace", "lines": ["a", "b"]}], "")
    helper.run([{"op": "ledger_append", "lines": ["c"]}], "")
    assert helper.run([{"op": "ledger_read"}], "") == ["a\nb\nc\n"]
    with pytest.raises(ValueError):
        helper.run([{"op": "rm -rf"}], "")


def test_verify_all_runs_every_scenario_and_summarises(
    docker: FakeDocker, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaos_cli import main as cli

    seen: list[int] = []

    def fake_verify_one(_: Testbed, __: Any, number: int) -> int:
        seen.append(number)
        return 1 if number == 5 else 0

    monkeypatch.setattr(cli, "verify_one", fake_verify_one)
    testbed = Testbed(docker, Secrets("t" * 32), ledger.Releases.load())  # type: ignore[arg-type]
    args = cli.parser().parse_args(["verify", "--all", "--hold", "900"])
    assert cli.cmd_verify(testbed, args) == 1
    assert seen == list(SCENARIOS)
    out = capsys.readouterr().out
    assert re.search(r"5 traffic-spike\s+FAIL", out) and re.search(r"1 memory-leak\s+PASS", out)
    with pytest.raises(ScenarioError):
        cli.cmd_verify(testbed, cli.parser().parse_args(["verify", "2", "--all"]))
