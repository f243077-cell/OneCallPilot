"""Scenario file (BENCH-001), run plan (BENCH-002, BENCH-003) and CSV schema (BENCH-007)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from bench import results
from bench.cli import main
from bench.plan import make_plan, read_plan, write_plan
from bench.scenarios import SCENARIOS_FILE, ScenarioFile, load

SCHEMA_DOC = Path(__file__).resolve().parents[1] / "schema" / "runs.csv.md"


def test_scenario_file_is_valid_and_matches_the_testbed() -> None:
    scenarios = load()
    assert [s.name for s in scenarios] == [
        "memory-leak", "bad-deploy", "db-pool", "cache-outage",
        "traffic-spike", "config-crash", "slow-dependency", "log-injection",
    ]  # fmt: skip
    assert [s.escalate for s in scenarios].count(True) == 2
    assert scenarios[1].expected == "rollback_deploy(service=api, target_release=1.4.0)"
    assert scenarios[6].expected == "escalate"
    assert all(s.timeout_minutes == 20 for s in scenarios)


@pytest.mark.parametrize(
    "change",
    [
        {"escalate": True},  # both an action and escalate
        {"accepted_categories": ["not_a_category"]},
        {"expected_action": {"action": "run_migration_rollback", "params": {}}},
        {"inject_command": "chaos inject other"},
    ],
)
def test_bad_scenarios_are_refused(change: dict[str, object]) -> None:
    data = yaml.safe_load(SCENARIOS_FILE.read_text())
    data["scenarios"][1] |= change
    with pytest.raises(ValueError):
        ScenarioFile.model_validate(data)


def test_plan_is_reproducible_and_balanced(tmp_path: Path) -> None:
    people = ["tanzeel", "observer2"]
    runs = make_plan(load(), 7, people)
    assert len(runs) == 48 and len({r.run_id for r in runs}) == 48
    assert make_plan(load(), 7, people) == runs
    assert make_plan(load(), 8, people) != runs
    assert all(r.operator != r.observer for r in runs)
    per = Counter((r.scenario_id, r.mode, r.operator) for r in runs)
    for s in range(1, 9):
        for mode in ("manual", "copilot"):
            assert all(per[(s, mode, p)] >= 1 for p in people)
    assert Counter(r.operator for r in runs) == {"tanzeel": 24, "observer2": 24}
    path = tmp_path / "plan.csv"
    write_plan(runs, path)
    assert read_plan(path) == runs


def test_plan_needs_two_people() -> None:
    with pytest.raises(ValueError):
        make_plan(load(), 1, ["only-me"])
    with pytest.raises(ValueError):
        make_plan(load(), 1, ["same", "same"])


def row(**overrides: object) -> dict[str, object]:
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    return {
        "run_id": "R7-01", "plan_seed": 7, "scenario_id": 2, "scenario_name": "bad-deploy",
        "mode": "copilot", "rep": 1, "operator": "tanzeel", "observer": "observer2",
        "t_inject": t0, "t_alert": t0, "escalated": False, "timed_out": False,
    } | overrides  # fmt: skip


def test_csv_rows_round_trip_with_the_schema_columns(tmp_path: Path) -> None:
    path = tmp_path / "runs.csv"
    results.append_row(row(), path)
    results.append_row(row(run_id="R7-02", notes="invalid_run"), path)
    rows = results.read_rows(path)
    assert [r["run_id"] for r in rows] == ["R7-01", "R7-02"]
    assert rows[0]["t_inject"] == "2026-11-02T10:00:00Z" and rows[0]["escalated"] == "false"
    assert path.read_text().splitlines()[0].split(",") == list(results.COLUMNS)


@pytest.mark.parametrize(
    "bad",
    [
        {"observer": "tanzeel"},
        {"mode": "auto"},
        {"bogus_column": 1},
        {"t_alert": "2026-11-02T10:00:00+02:00"},
    ],
)
def test_bad_rows_are_refused(tmp_path: Path, bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        results.append_row(row(**bad), tmp_path / "runs.csv")


def test_schema_document_lists_every_column() -> None:
    text = SCHEMA_DOC.read_text()
    assert all(f"`{c}`" in text for c in results.COLUMNS)


def test_cli_plan_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["plan", "--seed", "3", "--people", "a,b", "--out", str(tmp_path / "p.csv")])
    assert "48 runs" in capsys.readouterr().out
    results.append_row(row(), tmp_path / "runs.csv")
    main(["check", "--csv", str(tmp_path / "runs.csv")])
    assert "1 rows OK" in capsys.readouterr().out
    main(["scenarios"])
    assert "slow-dependency" in capsys.readouterr().out
