"""chaos reset | status | inject <scenario> | verify <scenario>  (architecture §2.13, §11.3).

Run from the repository root:  uv run --project chaos-shop/cli chaos <command>
"""

import argparse
import json
import sys
import time
from typing import Any

from chaos_cli.config import ALL_CONTAINERS, ConfigError, load_secrets
from chaos_cli.docker_ops import Docker, DockerOpsError
from chaos_cli.ledger import Releases
from chaos_cli.testbed import FIXABLE, SCENARIOS, ScenarioError, Testbed, scenario_number

INJECT_LIMIT_SECONDS = 30  # TB-006
RESET_LIMIT_SECONDS = 90  # TB-007


def build_testbed() -> Testbed:
    return Testbed(Docker(), load_secrets(), Releases.load())


def cmd_reset(testbed: Testbed, _: argparse.Namespace) -> int:
    result = testbed.reset()
    seconds, records = result["seconds"], result["ledger_records"]
    print(f"reset done in {seconds} s; ledger reseeded with {records} records")
    if result["seconds"] > RESET_LIMIT_SECONDS:
        print(f"WARNING: reset took longer than {RESET_LIMIT_SECONDS} s (TB-007)")
        return 1
    return 0


def cmd_status(testbed: Testbed, args: argparse.Namespace) -> int:
    snap = testbed.snapshot()
    if args.json:
        payload: dict[str, Any] = {
            "baseline": snap.baseline,
            "problems": snap.problems,
            "containers": {
                n: {
                    "status": s.status,
                    "health": s.health,
                    "restarts": s.restart_count,
                    "labels": s.labels,
                }
                for n, s in snap.states.items()
            },
            "flags": snap.flags,
            "ledger_tail": [r.model_dump(mode="json") for r in snap.ledger[-5:]],
        }
        print(json.dumps(payload, indent=2))
        return 0
    print(f"{'CONTAINER':<16} {'STATE':<11} {'HEALTH':<10} {'SERVICE':<9} {'RELEASE':<8} REPLICA")
    for name in ALL_CONTAINERS:
        state = snap.states[name]
        labels = state.labels
        print(
            f"{name:<16} {state.status:<11} {state.health or '-':<10} "
            f"{labels.get('oncallpilot.service', '-'):<9} "
            f"{labels.get('oncallpilot.release', '-'):<8} {labels.get('oncallpilot.replica', '-')}"
        )
    print("\nswitches:")
    for flag, value in snap.flags.items():
        print(f"  {flag}: {value}")
    print("\nledger (last 3):")
    for record in snap.ledger[-3:]:
        print(
            f"  {record.deployed_at:%Y-%m-%dT%H:%M:%SZ} {record.kind:<8} {record.service:<6} "
            f"{record.release:<6} x{record.replicas} by {record.deployed_by}"
        )
    print("\nbaseline: " + ("yes" if snap.baseline else "NO"))
    for problem in snap.problems:
        print(f"  - {problem}")
    return 0


def cmd_inject(testbed: Testbed, args: argparse.Namespace) -> int:
    number = scenario_number(args.scenario)
    started = time.monotonic()
    detail = testbed.inject(number)
    seconds = time.monotonic() - started
    print(f"injected {number} {SCENARIOS[number]} in {seconds:.1f} s: {detail}")
    if seconds > INJECT_LIMIT_SECONDS:
        print(f"WARNING: inject took longer than {INJECT_LIMIT_SECONDS} s (TB-006)")
        return 1
    return 0


def cmd_verify(testbed: Testbed, args: argparse.Namespace) -> int:
    """TB-008 for one scenario, or for all 8 in a row with ``--all`` (unattended run)."""
    if args.all == (args.scenario is not None):
        raise ScenarioError("give one scenario, or --all")
    if not args.all:
        return verify_one(testbed, args, scenario_number(args.scenario))
    started = time.monotonic()
    results: dict[int, int] = {}
    for number in SCENARIOS:
        try:
            results[number] = verify_one(testbed, args, number)
        except (ScenarioError, DockerOpsError) as exc:
            print(f"[{number} {SCENARIOS[number]}] ERROR: {exc}")
            results[number] = 2
    minutes = (time.monotonic() - started) / 60
    print(f"\nverify --all, hold {args.hold:.0f} s, {minutes:.0f} min:")
    for number, code in results.items():
        print(f"  {number} {SCENARIOS[number]:<16} {'PASS' if code == 0 else 'FAIL'}")
    return 0 if all(code == 0 for code in results.values()) else 1


def verify_one(testbed: Testbed, args: argparse.Namespace, number: int) -> int:
    """Inject, hold, assert still broken, fix through Docker, assert recovery."""
    name = f"{number} {SCENARIOS[number]}"
    try:
        if not args.no_reset:
            print(f"[{name}] reset: {testbed.reset()['seconds']} s")
        before = testbed.docker.states(ALL_CONTAINERS)
        started = time.monotonic()
        print(f"[{name}] inject: {testbed.inject(number)} ({time.monotonic() - started:.1f} s)")
        deadline = started + args.hold
        while (left := deadline - time.monotonic()) > 0:
            testbed.sleep(min(30.0, left))
            print(f"[{name}] holding: {max(0.0, deadline - time.monotonic()):.0f} s left")
        broken = testbed.broken(number, before)
        print(f"[{name}] still broken after {args.hold} s: {broken.ok} ({broken.detail})")
        if not broken.ok:
            return 1
        if number not in FIXABLE:
            print(
                f"[{name}] PASS: persisted {args.hold} s; no action fixes it, it must be escalated"
            )
            return 0
        print(f"[{name}] fix: {testbed.fix(number)}")
        fixed_at = time.monotonic()
        while True:
            check = testbed.recovered(number, before)
            elapsed = time.monotonic() - fixed_at
            if check.ok:
                print(f"[{name}] PASS: recovered {elapsed:.0f} s after the fix ({check.detail})")
                return 0
            if elapsed > args.recover:
                print(f"[{name}] FAIL: not recovered after {args.recover} s ({check.detail})")
                return 1
            testbed.sleep(5)
    finally:
        if not args.keep:
            print(f"[{name}] reset: {testbed.reset()['seconds']} s")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="chaos", description="Chaos Shop testbed control")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("reset", help="restore the baseline and reseed the deploy ledger (TB-007)")
    status = sub.add_parser("status", help="containers, labels, switches, ledger tail")
    status.add_argument("--json", action="store_true")
    scenarios = ", ".join(f"{n}={s}" for n, s in SCENARIOS.items())
    inject = sub.add_parser("inject", help=f"inject one scenario ({scenarios})")
    inject.add_argument("scenario")
    verify = sub.add_parser("verify", help="inject, hold, check, fix, check recovery (TB-008)")
    verify.add_argument("scenario", nargs="?")
    verify.add_argument(
        "--all", action="store_true", help="every scenario in a row, e.g. overnight --hold 900"
    )
    verify.add_argument("--hold", type=float, default=180.0, help="seconds to stay broken")
    verify.add_argument("--recover", type=float, default=120.0, help="seconds allowed to recover")
    verify.add_argument("--no-reset", action="store_true", help="skip the reset before inject")
    verify.add_argument("--keep", action="store_true", help="skip the reset at the end")
    return root


COMMANDS = {"reset": cmd_reset, "status": cmd_status, "inject": cmd_inject, "verify": cmd_verify}


def main(argv: list[str] | None = None) -> None:
    # Line-buffered, so a long unattended run writes its progress to a log file as it goes.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = parser().parse_args(argv)
    try:
        code = COMMANDS[args.command](build_testbed(), args)
    except (ConfigError, ScenarioError, DockerOpsError) as exc:
        print(f"chaos: {exc}", file=sys.stderr)
        code = 2
    sys.exit(code)
