"""``benchmark/results/runs.csv`` (BENCH-007; ``benchmark/schema/runs.csv.md``).

One row per completed run, appended; never edited by hand. All times are
UTC ISO-8601 (``…Z``); booleans are ``true``/``false``; empty means unknown.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNS_CSV = Path(__file__).resolve().parents[1] / "results" / "runs.csv"

IDENTITY = ("run_id", "plan_seed", "scenario_id", "scenario_name", "mode", "rep", "operator",
            "observer")  # fmt: skip
CONFIGURATION = ("git_sha", "llm_provider", "llm_model", "prompt_version",
                 "detector_config_version", "catalogue_version")  # fmt: skip
TIMESTAMPS = ("t_inject", "t_alert", "t_diagnosis", "t_proposal", "t_decision", "t_recovered")
OUTCOME = ("diagnosis_text", "diagnosis_correct", "top1_correct", "top3_correct",
           "proposal_action", "proposal_params", "expected_action", "action_correct",
           "escalated", "unsafe_action", "evidence_citations_total",
           "evidence_citations_valid", "timed_out", "notes")  # fmt: skip
COLUMNS = (*IDENTITY, *CONFIGURATION, *TIMESTAMPS, *OUTCOME)
BOOLEANS = {"diagnosis_correct", "top1_correct", "top3_correct", "action_correct", "escalated",
            "unsafe_action", "timed_out"}  # fmt: skip


def iso(at: datetime | None) -> str:
    return "" if at is None else at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"not a UTC time: {value!r}")
    return parsed


def _cell(column: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return iso(value)
    return str(value)


def check_row(row: dict[str, str]) -> None:
    """Raises ``ValueError`` for a row ``bench report`` could not read."""
    if tuple(row) != COLUMNS:
        raise ValueError("columns differ from the schema")
    for column in TIMESTAMPS:
        parse_time(row[column])
    for column in BOOLEANS:
        if row[column] not in ("", "true", "false"):
            raise ValueError(f"{column} must be true, false or empty")
    if row["mode"] not in ("manual", "copilot"):
        raise ValueError("mode must be manual or copilot")
    if not row["operator"] or row["operator"] == row["observer"]:
        raise ValueError("operator and observer must be two different people (BENCH-003)")


def append_row(values: dict[str, Any], path: Path = RUNS_CSV) -> dict[str, str]:
    unknown = set(values) - set(COLUMNS)
    if unknown:
        raise ValueError(f"unknown columns: {sorted(unknown)}")
    row = {column: _cell(column, values.get(column)) for column in COLUMNS}
    check_row(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow(row)
    return row


def read_rows(path: Path = RUNS_CSV) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        check_row(row)
    return rows
