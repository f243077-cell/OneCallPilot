"""`python -m oncallpilot_runner`: the runner process (architecture §2.10, §7.3).

Environment (architecture §7.3; values are never logged):
    RUNNER_REDIS_URL     copilot-redis as the `runner` ACL user
    ACTION_SIGNING_KEY   hex, >= 32 bytes: verifies execute requests
    RUNNER_LINK_KEY      hex, >= 32 bytes: verifies dry_run requests, signs results
    LEDGER_PATH          the deploy ledger (default /ledger/deploys.jsonl)
    DOCKER_HOST          tcp://socket-proxy-rw:2375; a socket path is refused
    DOCKER_API_VERSION   pinned API version, passed as `version=` (the proxy blocks /version)
    RUNNER_ADMIN_TOKEN   bearer token for the store's cache endpoints (clear_cache)

It refuses to start without any of these, the catalogue or the target allowlist.
"""

import argparse
import logging
import os
import re
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path

from oncallpilot_runner import log
from oncallpilot_runner.catalogue import Catalogue, CatalogueError
from oncallpilot_runner.checks import Checker
from oncallpilot_runner.consumer import Consumer
from oncallpilot_runner.docker_client import SdkDocker
from oncallpilot_runner.dry_run import DryRunner
from oncallpilot_runner.execute import Executor
from oncallpilot_runner.handlers import REGISTERED
from oncallpilot_runner.health import HealthChecker, http_probe
from oncallpilot_runner.ledger import ledger_releases
from oncallpilot_runner.shop_admin import fetch_cache_stats
from oncallpilot_runner.signing import Keys, SigningKeyError, key_from_hex
from oncallpilot_runner.streams import RedisStreams, Streams
from oncallpilot_runner.targets import Targets, TargetsError

logger = logging.getLogger("runner.main")

API_VERSION = re.compile(r"^1\.[0-9]{2}$")
MIN_ADMIN_TOKEN = 16


class SettingError(ValueError):
    """A required setting is missing or unsafe: the runner refuses to start."""


def docker_settings(environ: dict[str, str]) -> tuple[str, str]:
    host = environ.get("DOCKER_HOST", "")
    version = environ.get("DOCKER_API_VERSION", "")
    if not host.startswith("tcp://"):  # never the socket (CLAUDE.md §6.5)
        raise SettingError("DOCKER_HOST must be tcp://socket-proxy-rw:2375")
    if not API_VERSION.fullmatch(version):
        raise SettingError("DOCKER_API_VERSION must be pinned, e.g. 1.48")
    return host, version


def admin_token(environ: dict[str, str]) -> str:
    token = environ.get("RUNNER_ADMIN_TOKEN", "")
    if len(token) < MIN_ADMIN_TOKEN:
        raise SettingError(f"RUNNER_ADMIN_TOKEN must have at least {MIN_ADMIN_TOKEN} characters")
    return token


def now() -> datetime:
    return datetime.now(UTC)


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
        now=now,
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
        docker_host, api_version = docker_settings(dict(os.environ))
        token = admin_token(dict(os.environ))
    except (SigningKeyError, CatalogueError, TargetsError, SettingError) as exc:
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
    docker = SdkDocker.connect(docker_host, api_version)
    dry_runner = DryRunner(
        docker=docker,
        targets=targets,
        ledger_path=ledger,
        counters=streams,
        cache_stats=lambda: fetch_cache_stats(token),
        now=now,
    )
    health = HealthChecker(docker=docker, targets=targets, probe=http_probe, now=now)
    executor = Executor(docker, dry_runner, health, targets, ledger, now)
    consumer = Consumer(
        streams, checker, socket.gethostname(), dry_runner=dry_runner, executor=executor
    )
    health.tick = consumer.beat  # long waits keep the heartbeat (healthcheck) fresh
    logger.info(f"runner starting; catalogue {catalogue.version}, Docker API {api_version}")
    consumer.run_forever()


if __name__ == "__main__":
    main()
