"""The `ocp` entry point. Subcommands are the modules in `ocp_cli/commands/`.

Each command module defines NAME, HELP, `add_arguments(parser)` and
`run(args, stack) -> int`. A new command is a new file there and needs no edit
here, so commands owned by different developers (up, down and doctor by
Tanzeel; seed-incident and smoke by Usman) never touch the same file.

    uv tool install ./tools/ocp && ocp up        # or: uv run --project tools/ocp ocp up
"""

import argparse
import importlib
import pkgutil
import sys
from types import ModuleType

from ocp_cli import commands
from ocp_cli.stack import Stack, StackError


def command_modules() -> dict[str, ModuleType]:
    found: dict[str, ModuleType] = {}
    for info in sorted(pkgutil.iter_modules(commands.__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{commands.__name__}.{info.name}")
        found[module.NAME] = module
    return found


def parser(modules: dict[str, ModuleType]) -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="ocp", description="OnCallPilot stack control")
    sub = root.add_subparsers(dest="command", required=True)
    for name, module in modules.items():
        module.add_arguments(sub.add_parser(name, help=module.HELP))
    return root


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    modules = command_modules()
    args = parser(modules).parse_args(argv)
    try:
        code = int(modules[args.command].run(args, Stack.discover()))
    except StackError as exc:
        print(f"ocp: {exc}", file=sys.stderr)
        code = 2
    sys.exit(code)
