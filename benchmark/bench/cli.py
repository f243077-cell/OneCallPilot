"""``bench`` command line (BENCH-002, BENCH-007).

- ``bench scenarios``: the 8 scenarios and their expected outcomes.
- ``bench plan --seed N --people a,b``: write ``results/plan-N.csv`` (48 runs).
- ``bench check``: validate ``results/runs.csv`` against the schema.

``bench run``, ``bench mark`` and the audits come with tasks B3.6–B3.7 (``next.md``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from bench import results
from bench.plan import make_plan, write_plan
from bench.scenarios import load

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
