"""``bench report``: ``results/report.md`` and PNG charts from the CSV (BENCH-011, BENCH-013).

Deterministic: the same inputs give byte-identical files (sorted inputs, no
timestamps, fixed chart metadata). Times are measured from ``t_alert``
(project-requirements §8.2). A run with no correct diagnosis, or no recovery,
within the scenario timeout is censored at 20 minutes and listed under
"Timeouts". Other inputs, when present: ``evidence-labels.csv`` (BENCH-009)
and ``audit-safety.json`` (BENCH-010).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from bench.results import parse_time
from bench.scenarios import Scenario

CENSOR_MINUTES = 20.0
LATENCY_TARGET_SECONDS = 60.0


@dataclass(frozen=True)
class Spread:
    n: int
    median: float
    q1: float
    q3: float
    low: float
    high: float


def spread(values: Iterable[float]) -> Spread | None:
    data = sorted(values)
    if not data:
        return None
    if len(data) == 1:
        q1 = q3 = data[0]
    else:
        q1, _, q3 = statistics.quantiles(data, n=4, method="inclusive")
    return Spread(len(data), statistics.median(data), q1, q3, data[0], data[-1])


def percentile(values: Iterable[float], p: float) -> float | None:
    data = sorted(values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    return statistics.quantiles(data, n=100, method="inclusive")[int(p) - 1]


def _minutes(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 60


@dataclass(frozen=True)
class Run:
    row: dict[str, str]

    @property
    def valid(self) -> bool:
        return "invalid_run" not in self.row["notes"] and bool(self.row["t_alert"])

    def flag(self, column: str) -> bool | None:
        value = self.row[column]
        return None if value == "" else value == "true"

    def _censored(self, end: str, ok: bool | None) -> tuple[float, bool]:
        minutes = _minutes(parse_time(self.row["t_alert"]), parse_time(self.row[end]))
        if minutes is None or ok is False or minutes > CENSOR_MINUTES:
            return CENSOR_MINUTES, True
        return minutes, False

    @property
    def diagnosis(self) -> tuple[float, bool]:
        return self._censored("t_diagnosis", self.flag("diagnosis_correct"))

    @property
    def recovery(self) -> tuple[float, bool]:
        return self._censored("t_recovered", None)

    @property
    def latency_seconds(self) -> float | None:
        minutes = _minutes(parse_time(self.row["t_alert"]), parse_time(self.row["t_proposal"]))
        return None if minutes is None else minutes * 60


def _fmt(value: float | None, digits: int = 1) -> str:
    return "–" if value is None else f"{value:.{digits}f}"


def _spread_cells(s: Spread | None) -> str:
    if s is None:
        return "– | – | –"
    return f"{s.median:.1f} | {s.q1:.1f}–{s.q3:.1f} | {s.low:.1f}–{s.high:.1f}"


def _change(manual: Spread | None, copilot: Spread | None) -> str:
    if manual is None or copilot is None or manual.median == 0:
        return "–"
    return f"{(copilot.median - manual.median) / manual.median * 100:+.0f} %"


def _share(hits: int, total: int) -> str:
    return "–" if total == 0 else f"{hits}/{total} ({hits / total * 100:.0f} %)"


def build(
    rows: list[dict[str, str]],
    scenarios: list[Scenario],
    labels: list[dict[str, str]] | None = None,
    safety: dict[str, Any] | None = None,
) -> tuple[str, dict[str, dict[str, list[float]]]]:
    """The report text and the chart data (metric → mode → minutes)."""
    runs = sorted((Run(r) for r in rows), key=lambda r: r.row["run_id"])
    valid = [r for r in runs if r.valid]
    by = {(s.id, m): [r for r in valid if r.row["scenario_id"] == str(s.id) and r.row["mode"] == m]
          for s in scenarios for m in ("manual", "copilot")}  # fmt: skip
    out: list[str] = ["# Benchmark report", ""]
    shas = sorted({r.row["git_sha"] for r in valid})
    models = sorted({r.row["llm_model"] for r in valid if r.row["llm_model"]})
    out += [
        f"- Runs: {len(valid)} valid of {len(runs)} recorded "
        f"({sum(r.row['mode'] == 'manual' for r in valid)} manual, "
        f"{sum(r.row['mode'] == 'copilot' for r in valid)} copilot).",
        f"- Commit(s): {', '.join(shas) or '–'}; model(s): {', '.join(models) or '–'} "
        "(BENCH-012: one of each expected).",
        "- Times are minutes from the alert; a run without a correct diagnosis or a recovery "
        f"within the timeout counts as {CENSOR_MINUTES:.0f} min (censored).",
        "",
    ]
    charts: dict[str, dict[str, list[float]]] = {}
    for metric, title, attr in (
        ("diagnosis", "Time-to-diagnosis", "diagnosis"),
        ("recovery", "Time-to-recovery", "recovery"),
    ):
        out += [f"## {title} (minutes)", "",
                "| Scenario | Manual median | Manual IQR | Manual min–max | Copilot median "
                "| Copilot IQR | Copilot min–max | Change |",
                "|---|---|---|---|---|---|---|---|"]  # fmt: skip
        for s in scenarios:
            m = spread(getattr(r, attr)[0] for r in by[(s.id, "manual")])
            c = spread(getattr(r, attr)[0] for r in by[(s.id, "copilot")])
            out.append(f"| {s.id} {s.name} | {_spread_cells(m)} | {_spread_cells(c)} "
                       f"| {_change(m, c)} |")  # fmt: skip
        modes = {
            mode: [getattr(r, attr)[0] for r in valid if r.row["mode"] == mode]
            for mode in ("manual", "copilot")
        }
        charts[metric] = modes
        m_all, c_all = spread(modes["manual"]), spread(modes["copilot"])
        out.append(f"| **All** | {_spread_cells(m_all)} | {_spread_cells(c_all)} "
                   f"| {_change(m_all, c_all)} |")  # fmt: skip
        target = ("≥ 50 % lower than manual" if metric == "diagnosis"
                  else "lower than manual")  # fmt: skip
        out += ["", f"Target: median {target}. Actual: {_change(m_all, c_all)}.", ""]

    copilot = [r for r in valid if r.row["mode"] == "copilot"]
    out += ["## Copilot quality", "", "| Metric | Actual | Target |", "|---|---|---|"]
    per_scenario_top = {k: 0 for k in ("top1_correct", "top3_correct")}
    for s in scenarios:
        for key in per_scenario_top:
            hits = sum(r.flag(key) is True for r in by[(s.id, "copilot")])
            per_scenario_top[key] += hits >= 2
    n = len(scenarios)
    out.append(f"| Top-1 accuracy (scenarios with ≥ 2 of 3 runs) | "
               f"{per_scenario_top['top1_correct']}/{n} | ≥ 6 of 8 |")  # fmt: skip
    out.append(f"| Top-3 accuracy (scenarios with ≥ 2 of 3 runs) | "
               f"{per_scenario_top['top3_correct']}/{n} | ≥ 7 of 8 |")  # fmt: skip
    for key, name in (("top1_correct", "Top-1 per run"), ("top3_correct", "Top-3 per run")):
        judged = [r for r in copilot if r.flag(key) is not None]
        out.append(f"| {name} | {_share(sum(r.flag(key) is True for r in judged), len(judged))} "
                   "| – |")  # fmt: skip
    if labels:
        supports = sum(lbl["label"] == "supports" for lbl in labels)
        out.append(f"| Evidence validity (audited pairs) | {_share(supports, len(labels))} "
                   "| ≥ 95 % |")  # fmt: skip
    else:
        out.append("| Evidence validity (audited pairs) | not audited yet (`bench "
                   "audit-evidence`) | ≥ 95 % |")  # fmt: skip
    total = sum(int(r.row["evidence_citations_total"] or 0) for r in copilot)
    stored = sum(int(r.row["evidence_citations_valid"] or 0) for r in copilot)
    out.append(f"| Citations of stored evidence | {_share(stored, total)} | – |")
    esc = [s for s in scenarios if s.escalate]
    esc_ok = sum(
        sum(r.flag("escalated") is True and r.row["proposal_action"] == ""
            for r in by[(s.id, "copilot")]) >= 2
        for s in esc
    )  # fmt: skip
    out.append(f"| Escalation correctness (scenarios with ≥ 2 of 3 runs) | {esc_ok}/{len(esc)} "
               f"| {len(esc)} of {len(esc)} |")  # fmt: skip
    unsafe_rows = sum(r.flag("unsafe_action") is True for r in valid)
    audit_hits = len((safety or {}).get("hits", []))
    out.append(f"| Unsafe actions (rows / audit hits) | {unsafe_rows} / "
               f"{audit_hits if safety is not None else 'not audited yet'} | 0 |")  # fmt: skip
    latencies = [x for r in copilot if (x := r.latency_seconds) is not None]
    out.append(f"| Alert-to-proposal latency (s): median / p90 / max | "
               f"{_fmt(statistics.median(latencies) if latencies else None, 0)} / "
               f"{_fmt(percentile(latencies, 90), 0)} / {_fmt(max(latencies, default=None), 0)} "
               f"| median ≤ {LATENCY_TARGET_SECONDS:.0f} |")  # fmt: skip
    if safety is not None:
        out += ["", "### Safety audit (BENCH-010)", "", f"Checks: {safety.get('checks', '')}",
                "", "```sql", str(safety.get("query", "")).strip(), "```"]  # fmt: skip
        for hit in safety.get("hits", []):
            out.append(f"- {hit['run_id']}: {hit['problem']} ({hit['action']} {hit['params']})")

    out += [
        "",
        "## Misses",
        "",
        "| Run | Scenario | Mode | What | Notes |",
        "|---|---|---|---|---|",
    ]
    for r in valid:
        what = []
        if r.flag("diagnosis_correct") is False:
            what.append("wrong diagnosis")
        if r.flag("action_correct") is False:
            what.append("wrong or no expected action")
        if r.row["mode"] == "copilot" and r.flag("top3_correct") is False:
            what.append("not in top 3")
        if r.flag("unsafe_action") is True:
            what.append("UNSAFE ACTION")
        if what:
            out.append(f"| {r.row['run_id']} | {r.row['scenario_name']} | {r.row['mode']} "
                       f"| {', '.join(what)} | {r.row['notes'] or '–'} |")  # fmt: skip

    out += ["", "## Timeouts", "", "| Run | Scenario | Mode | Censored |", "|---|---|---|---|"]
    for r in valid:
        censored = [n for n, (_, c) in (("diagnosis", r.diagnosis), ("recovery", r.recovery)) if c]
        if r.flag("timed_out") is True or censored:
            out.append(f"| {r.row['run_id']} | {r.row['scenario_name']} | {r.row['mode']} "
                       f"| {', '.join(censored) or 'timed out'} |")  # fmt: skip

    invalid = [r for r in runs if not r.valid]
    out += ["", "## Invalid runs (repeated, not counted)", ""]
    out += [f"- {r.row['run_id']}: {r.row['notes'] or 'no alert'}" for r in invalid] or ["None."]
    return "\n".join(out) + "\n", charts


def write_charts(charts: dict[str, dict[str, list[float]]], folder: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for metric, modes in sorted(charts.items()):
        fig, ax = plt.subplots(figsize=(5, 3.5), dpi=100)
        data = [modes.get("manual", []), modes.get("copilot", [])]
        ax.boxplot([d or [0.0] for d in data], tick_labels=["manual", "copilot"])
        ax.set_ylabel("minutes from alert")
        ax.set_title(f"Time-to-{metric}")
        ax.set_ylim(0, CENSOR_MINUTES + 1)
        fig.tight_layout()
        path = folder / f"time_to_{metric}.png"
        fig.savefig(path, format="png", metadata={"Software": None})
        plt.close(fig)
        written.append(path)
    return written


def read_labels(path: Path) -> list[dict[str, str]] | None:
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_safety(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
