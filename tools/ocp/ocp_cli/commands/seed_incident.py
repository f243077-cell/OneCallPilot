"""ocp seed-incident: write a fixture timeline into the database and publish its events (B1.4).

Runs `python -m oncallpilot.seed` once in the backend image (Compose service
`backend-seed`, profile `tools`), on copilot_net so it reaches copilot-redis,
with the timeline's folder mounted read-only. The incident then appears in
`GET /incidents` and its events on `/ws/incidents`, without the detector or
the agent (phases.md §4). Needs DATABASE_URL and REDIS_URL in .env.

    ocp seed-incident bad_deploy_success            # a name from contracts/fixtures/timelines
    ocp seed-incident path/to/timeline.json --new-ids --realtime
"""

import argparse
from pathlib import Path

from ocp_cli.stack import Stack, StackError

NAME = "seed-incident"
HELP = "write a fixture timeline to the database and publish its events (B1.4)"
SERVICE = "backend-seed"
TIMELINES = Path("contracts") / "fixtures" / "timelines"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "timeline", help="a timeline name (e.g. bad_deploy_success) or a .json path"
    )
    parser.add_argument(
        "--new-ids", action="store_true", help="seed under fresh UUIDs (to seed it again)"
    )
    parser.add_argument("--realtime", action="store_true", help="honour each step's delay_ms")
    parser.add_argument("--no-build", action="store_true", help="use the existing backend image")


def resolve(stack: Stack, timeline: str) -> Path:
    given = Path(timeline)
    candidates = [
        given,
        stack.root / TIMELINES / timeline,
        stack.root / TIMELINES / f"{timeline}.json",
    ]
    for candidate in candidates:
        if candidate.suffix == ".json" and candidate.is_file():
            return candidate.resolve()
    names = sorted(p.stem for p in (stack.root / TIMELINES).glob("*.json"))
    raise StackError(f"no timeline {timeline!r}; fixture timelines: {', '.join(names)}")


def run(args: argparse.Namespace, stack: Stack) -> int:
    if not (stack.root / ".env").is_file():
        raise StackError("no .env in the repository root; copy .env.example and fill it in")
    path = resolve(stack, args.timeline)
    command = [*stack.compose("copilot", "tools"), "run", "--rm"]
    if not args.no_build:
        command.append("--build")
    command += ["-v", f"{path.parent}:/timelines:ro", SERVICE, f"/timelines/{path.name}"]
    if args.new_ids:
        command.append("--new-ids")
    if args.realtime:
        command.append("--realtime")
    print(f"[ocp seed-incident] seeding {path.name}")
    stack.check(command, capture=False)
    return 0
