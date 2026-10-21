"""``bench plan --seed``: 48 runs in a random but reproducible order (BENCH-002, BENCH-003).

8 scenarios × 3 repetitions × 2 modes. Operators are balanced: for each
scenario the operators alternate across repetitions, and the order flips
between the two modes, so with two operators each one has at least one run
of every scenario in every mode. The observer is always someone else.
"""

from __future__ import annotations

import csv
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from bench.scenarios import Scenario

Mode = Literal["manual", "copilot"]
MODES: tuple[Mode, ...] = ("manual", "copilot")
REPETITIONS = 3
PLAN_COLUMNS = ("run_id", "plan_seed", "scenario_id", "scenario_name", "mode", "rep",
                "operator", "observer")  # fmt: skip


@dataclass(frozen=True)
class PlannedRun:
    run_id: str
    plan_seed: int
    scenario_id: int
    scenario_name: str
    mode: Mode
    rep: int
    operator: str
    observer: str


def make_plan(scenarios: list[Scenario], seed: int, people: list[str]) -> list[PlannedRun]:
    if len(people) < 2 or len(set(people)) != len(people):
        raise ValueError("the plan needs at least two different people (operator ≠ observer)")
    slots: list[tuple[Scenario, Mode, int, str]] = []
    for s in scenarios:
        for m, mode in enumerate(MODES):
            for rep in range(1, REPETITIONS + 1):
                operator = people[(s.id + m + rep) % len(people)]
                slots.append((s, mode, rep, operator))
    random.Random(seed).shuffle(slots)
    runs = []
    for i, (s, mode, rep, operator) in enumerate(slots, start=1):
        observer = people[(people.index(operator) + 1) % len(people)]
        runs.append(
            PlannedRun(f"R{seed}-{i:02d}", seed, s.id, s.name, mode, rep, operator, observer)
        )
    return runs


def write_plan(runs: list[PlannedRun], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PLAN_COLUMNS)
        writer.writeheader()
        for run in runs:
            writer.writerow(asdict(run))


def _mode(value: str) -> Mode:
    if value not in MODES:
        raise ValueError(f"unknown mode {value!r}")
    return "manual" if value == "manual" else "copilot"


def read_plan(path: Path) -> list[PlannedRun]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            PlannedRun(
                row["run_id"],
                int(row["plan_seed"]),
                int(row["scenario_id"]),
                row["scenario_name"],
                _mode(row["mode"]),
                int(row["rep"]),
                row["operator"],
                row["observer"],
            )
            for row in csv.DictReader(handle)
        ]
