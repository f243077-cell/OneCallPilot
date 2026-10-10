"""`python -m oncallpilot_runner`: the runner process (architecture §2.10, §7.3).

Environment (architecture §7.3; values are never logged):
    RUNNER_REDIS_URL     copilot-redis as the `runner` ACL user
    ACTION_SIGNING_KEY   hex, >= 32 bytes: verifies execute requests
    RUNNER_LINK_KEY      hex, >= 32 bytes: verifies dry_run requests, signs results
    LEDGER_PATH          the deploy ledger (default /ledger/deploys.jsonl)
DOCKER_HOST, DOCKER_API_VERSION and RUNNER_ADMIN_TOKEN are used from task A2.3 on.

It refuses to start without both keys, the catalogue or the target allowlist.
"""

import argparse
import logging
import os
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from oncallpilot_runner import log
from oncallpilot_runner.catalogue import Catalogue, CatalogueError
from oncallpilot_runner.checks import Checker
from oncallpilot_runner.consumer import Consumer
from oncallpilot_runner.handlers import REGISTERED
from oncallpilot_runner.ledger import ledger_releases
from oncallpilot_runner.signing import Keys, SigningKeyError, key_from_hex
from oncallpilot_runner.streams import RedisStreams, Streams
from oncallpilot_runner.targets import Targets, TargetsError

logger = logging.getLogger("runner.main")


def build_checker(
    streams: Streams, catalogue: Catalogue, targets: Targets, keys: Keys, ledger: Path
) -> Checker:
    def runtime_values(source: str, params: dict[str, str | int]) -> set[str]:
        if source != "ledger_releases":
            raise ValueError(f"unknown values_from {source!r}")
        return ledger_releases(ledger, targets, str(params.get("service", "")))

    return Checker(
        keys=keys,
        catalogue=catalogue,
        targets=targets,
        runtime_values=runtime_values,
        set_nx=streams.set_nx,
        now=lambda: datetime.now(UTC),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="oncallpilot_runner")
    parser.add_argument("--catalogue", type=Path, default=Path("/app/contracts/actions.yaml"))
    parser.add_argument("--targets", type=Path, default=Path("/app/targets.yaml"))
    args = parser.parse_args(argv)
    log.configure()
    try:
        keys = Keys(
            action=key_from_hex("ACTION_SIGNING_KEY", os.environ.get("ACTION_SIGNING_KEY")),
            link=key_from_hex("RUNNER_LINK_KEY", os.environ.get("RUNNER_LINK_KEY")),
        )
        if keys.action == keys.link:
            raise SigningKeyError("ACTION_SIGNING_KEY and RUNNER_LINK_KEY must differ")
        catalogue = Catalogue.load(args.catalogue)
        targets = Targets.load(args.targets)
        url = os.environ["RUNNER_REDIS_URL"]
    except (SigningKeyError, CatalogueError, TargetsError) as exc:
        logger.critical(f"cannot start: {exc}")
        sys.exit(2)
    except KeyError:
        logger.critical("cannot start: RUNNER_REDIS_URL is not set")
        sys.exit(2)
    if catalogue.enabled != REGISTERED:
        logger.critical("cannot start: handlers differ from the catalogue's enabled actions")
        sys.exit(2)
    streams = RedisStreams.from_url(url)
    ledger = Path(os.environ.get("LEDGER_PATH", "/ledger/deploys.jsonl"))
    checker = build_checker(streams, catalogue, targets, keys, ledger)
    logger.info(f"runner starting; catalogue {catalogue.version}")
    Consumer(streams, checker, socket.gethostname()).run_forever()


if __name__ == "__main__":
    main()
