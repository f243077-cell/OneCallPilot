"""``bench`` command line (BENCH-002, BENCH-007).

- ``bench scenarios``: the 8 scenarios and their expected outcomes.
- ``bench plan --seed N --people a,b``: write ``results/plan-N.csv`` (48 runs).
- ``bench check``: validate ``results/runs.csv`` against the schema.
- ``bench run RUN_ID --plan results/plan-N.csv``: one run (BENCH-004), one CSV row.
- ``bench mark diagnosis TEXT | decision | note TEXT | action-correct true|false``:
  the observer's marks for the run in progress.

``bench run`` reads ``DATABASE_URL``, ``REDIS_URL``, ``SUPABASE_URL``,
``SUPABASE_ANON_KEY``, ``OCP_TEST_EMAIL``, ``OCP_TEST_PASSWORD`` and optionally
``OCP_API_URL`` (default ``http://127.0.0.1:8000``) and ``BENCH_CHAOS_CMD``
(default ``uv run --project chaos-shop/cli chaos``) from the environment.
- ``bench audit-safety``: ``results/audit-safety.json`` (BENCH-010; needs ``DATABASE_URL``).
- ``bench audit-evidence --labeler NAME``: label (hypothesis, evidence) pairs into
  ``results/evidence-labels.csv`` (BENCH-009; needs ``DATABASE_URL``).
- ``bench report``: ``results/report.md`` and ``results/charts/*.png`` (BENCH-011, BENCH-013).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from bench import audit, report, results
from bench.api import Api, ApiError
from bench.db import PostgresDb
from bench.marks import Marks
from bench.plan import make_plan, read_plan, write_plan
from bench.run import BenchAbort, Deps, run_one
from bench.scenarios import load
from bench.stack import Stack, StackError, SubprocessCommands, redis_password

RESULTS = Path(__file__).resolve().parents[1] / "results"


def main(argv: Sequence[str] | None = None) -> None:
    root = argparse.ArgumentParser(prog="bench", description=__doc__.splitlines()[0])
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("scenarios", help="list the scenarios and expected outcomes (BENCH-001)")
    plan = sub.add_parser("plan", help="write a seeded 48-run plan (BENCH-002)")
    plan.add_argument("--seed", type=int, required=True)
    plan.add_argument("--people", required=True, help="comma-separated, at least two people")
    plan.add_argument("--out", type=Path, default=None)
    check = sub.add_parser("check", help="validate results/runs.csv (BENCH-007)")
    check.add_argument("--csv", type=Path, default=results.RUNS_CSV)
    run = sub.add_parser("run", help="carry out one planned run (BENCH-004)")
    run.add_argument("run_id")
    run.add_argument("--plan", type=Path, required=True)
    mark = sub.add_parser("mark", help="observer mark for the run in progress (BENCH-007)")
    mark.add_argument("kind", choices=["diagnosis", "decision", "note", "action-correct"])
    mark.add_argument("text", nargs="?")
    sub.add_parser("audit-safety", help="check every proposal and execution (BENCH-010)")
    audit_ev = sub.add_parser("audit-evidence", help="label cited evidence (BENCH-009)")
    audit_ev.add_argument("--labeler", required=True, help="your name; never your own runs")
    sub.add_parser("report", help="write results/report.md and charts (BENCH-011)")
    args = root.parse_args(argv)

    if args.command == "scenarios":
        for s in load():
            print(f"{s.id}  {s.name:<16} {s.expected:<55} {s.timeout_minutes} min")
    elif args.command == "plan":
        people = [p.strip() for p in args.people.split(",") if p.strip()]
        try:
            runs = make_plan(load(), args.seed, people)
        except ValueError as exc:
            sys.exit(f"bench: {exc}")
        out = args.out or RESULTS / f"plan-{args.seed}.csv"
        write_plan(runs, out)
        print(f"{len(runs)} runs written to {out}")
    elif args.command == "check":
        try:
            rows = results.read_rows(args.csv)
        except (OSError, ValueError) as exc:
            sys.exit(f"bench: {args.csv}: {exc}")
        print(f"{len(rows)} rows OK")
    elif args.command == "mark":
        try:
            Marks().mark(args.kind, args.text)
        except ValueError as exc:
            sys.exit(f"bench: {exc}")
        print(f"marked {args.kind}")
    elif args.command == "run":
        _run(args.run_id, args.plan)
    elif args.command == "audit-safety":
        data = audit.safety(PostgresDb(_env("DATABASE_URL")))
        (RESULTS / "audit-safety.json").write_text(
            json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"{len(data['hits'])} unsafe action(s) found")
    elif args.command == "audit-evidence":
        operators = {r["run_id"]: r["operator"] for r in results.read_rows()}
        count = audit.label_evidence(
            PostgresDb(_env("DATABASE_URL")), operators, args.labeler,
            RESULTS / "evidence-labels.csv",
        )  # fmt: skip
        print(f"{count} pair(s) labelled")
    elif args.command == "report":
        text, charts = report.build(
            results.read_rows(), load(),
            report.read_labels(RESULTS / "evidence-labels.csv"),
            report.read_safety(RESULTS / "audit-safety.json"),
        )  # fmt: skip
        (RESULTS / "report.md").write_text(text, encoding="utf-8")
        report.write_charts(charts, RESULTS / "charts")
        print(f"report written to {RESULTS / 'report.md'}")


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"bench: {name} is not set")
    return value


def _run(run_id: str, plan_path: Path) -> None:
    planned = next((r for r in read_plan(plan_path) if r.run_id == run_id), None)
    if planned is None:
        sys.exit(f"bench: {run_id} is not in {plan_path}")
    scenario = next(s for s in load() if s.id == planned.scenario_id)
    chaos = shlex.split(os.environ.get("BENCH_CHAOS_CMD", "uv run --project chaos-shop/cli chaos"))
    with httpx.Client() as http:
        deps = Deps(
            stack=Stack(SubprocessCommands(), redis_password(_env("REDIS_URL")), chaos_cmd=chaos),
            api=Api(
                http,
                os.environ.get("OCP_API_URL", "http://127.0.0.1:8000"),
                _env("SUPABASE_URL"),
                _env("SUPABASE_ANON_KEY"),
                _env("OCP_TEST_EMAIL"),
                _env("OCP_TEST_PASSWORD"),
            ),  # fmt: skip
            db=PostgresDb(_env("DATABASE_URL")),
            marks=Marks(),
            clock=lambda: datetime.now(UTC),
            sleep=time.sleep,
        )
        try:
            row = run_one(planned, scenario, deps)
        except (BenchAbort, StackError, ApiError) as exc:
            sys.exit(f"bench: run {run_id} aborted, nothing written: {exc}")
    results.append_row(row)
    print(f"{run_id}: row written ({row.get('notes') or 'ok'})")
