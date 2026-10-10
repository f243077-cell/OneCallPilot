"""ocp down: stop and remove the stack's containers, slots included.

Volumes stay (the store database, the deploy ledger), so `ocp up` starts from
the same data; `chaos reset` rewrites the ledger anyway.
"""

import argparse

from ocp_cli.stack import PROFILES, SLOT_PROFILE, Stack

NAME = "down"
HELP = "stop and remove the stack's containers (volumes are kept)"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    pass


def run(args: argparse.Namespace, stack: Stack) -> int:
    print("[ocp down] stopping and removing the containers")
    stack.check([*stack.compose(*PROFILES, SLOT_PROFILE), "down"], capture=False)
    return 0
