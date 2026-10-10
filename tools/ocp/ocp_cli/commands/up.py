"""ocp up: start the stack, reset the testbed, wait for health, print the LAN URL.

architecture §12.1: `docker compose up -d --build` with all four profiles, then
`chaos reset`, then wait for health. The testbed's stopped slots are created
here too (week-1.md, "Compose profiles").
"""

import argparse
import time

from ocp_cli import policy
from ocp_cli.lan import lan_address
from ocp_cli.stack import COMPOSE_MIN, PROFILES, SLOT_PROFILE, Stack, StackError

NAME = "up"
HELP = "start the stack, reset the testbed, wait for health, print the LAN URL"

HEALTH_TIMEOUT = 240.0
POLL_SECONDS = 3.0
API_PORT = 8000


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-build", action="store_true", help="use the images already built (offline)"
    )
    parser.add_argument(
        "--timeout", type=float, default=HEALTH_TIMEOUT, help="seconds to wait for health"
    )


def run(args: argparse.Namespace, stack: Stack) -> int:
    version = stack.compose_version()
    if version < COMPOSE_MIN:
        raise StackError(f"Docker Compose {'.'.join(map(str, version))} is older than 2.20")
    if not (stack.root / ".env").is_file():
        raise StackError("no .env in the repository root; copy .env.example and fill it in")

    started = time.monotonic()
    print(f"[ocp up] starting profiles {', '.join(PROFILES)}")
    up = [*stack.compose(*PROFILES), "up", "-d", "--wait", "--wait-timeout", f"{args.timeout:.0f}"]
    up += ["--no-build"] if args.no_build else ["--build"]
    stack.check(up, capture=False)

    print("[ocp up] creating the stopped testbed slots")
    stack.check([*stack.compose(SLOT_PROFILE), "create"], capture=False)

    print("[ocp up] chaos reset")
    stack.check(["uv", "run", "--project", "chaos-shop/cli", "chaos", "reset"], capture=False)

    expected = stack.expected_services()
    problems = wait_healthy(stack, expected, args.timeout)
    if problems:
        print("[ocp up] not healthy:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"[ocp up] {len(expected)} services healthy in {time.monotonic() - started:.0f} s")
    print_lan_url(expected)
    return 0


def wait_healthy(
    stack: Stack, expected: list[str], timeout: float, poll: float = POLL_SECONDS
) -> list[str]:
    deadline = time.monotonic() + timeout
    while True:
        problems = policy.health_problems(expected, stack.containers())
        if not problems or time.monotonic() >= deadline:
            return problems
        time.sleep(poll)


def print_lan_url(expected: list[str]) -> None:
    address = lan_address()
    if address is None:
        print("[ocp up] no LAN address found (is Wi-Fi connected?)")
        return
    note = "" if "backend-api" in expected else "  (backend-api is not in the stack yet)"
    print("[ocp up] for the app on the phone (same Wi-Fi):")
    print(f"  --dart-define=API_BASE_URL=http://{address}:{API_PORT}{note}")
    print(f"  --dart-define=WS_URL=ws://{address}:{API_PORT}/ws/incidents")
