"""Shared fixtures: the real catalogue and allowlist, a ledger, keys, a clock, and a
fake Docker that looks like the testbed (slots of two api and two worker releases).

Keys are the shared vector's TEST-ONLY keys when the vector is available
(OCP_CONTRACTS_DIR, or ../contracts after the contracts merge), otherwise
random keys of the same length, so every check runs in CI either way.
"""

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from oncallpilot_runner.catalogue import Catalogue
from oncallpilot_runner.checks import Checker
from oncallpilot_runner.dry_run import DryRunner
from oncallpilot_runner.ledger import ledger_releases
from oncallpilot_runner.signing import Keys
from oncallpilot_runner.targets import Targets
from tests.fakes import FakeDockerClient, FakeStreams
from tests.publish_signed import vector_keys, vector_path

RUNNER = Path(__file__).resolve().parents[1]
CATALOGUE = RUNNER.parent / "contracts" / "actions.yaml"
TARGETS = RUNNER / "targets.yaml"
NOW = datetime(2026, 10, 14, 9, 1, 59, tzinfo=UTC)

# api: 1.4.0 was deployed, then 1.5.0 (active). worker: 2.1.0 active.
LEDGER_LINES = [
    '{"service":"api","release":"1.4.0","kind":"deploy","replicas":1}',
    '{"service":"worker","release":"2.1.0","kind":"deploy","replicas":1}',
    '{"service":"api","release":"1.5.0","kind":"deploy","replicas":1}',
]
CACHE_STATS = {
    "catalog": {"present": True, "ttl_seconds": 50},
    "pricing": {"entries": 12, "ttl_seconds": 40},
}


@pytest.fixture
def keys() -> Keys:
    path = vector_path()
    if path is not None:
        return vector_keys(path)
    return Keys(action=secrets.token_bytes(32), link=secrets.token_bytes(32))


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    path = tmp_path / "deploys.jsonl"
    path.write_text("\n".join(LEDGER_LINES) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def clock() -> list[datetime]:
    """A settable clock: tests move `clock[0]`."""
    return [NOW]


@pytest.fixture
def streams() -> FakeStreams:
    return FakeStreams()


@pytest.fixture
def checker(keys: Keys, ledger: Path, clock: list[datetime], streams: FakeStreams) -> Checker:
    targets = Targets.load(TARGETS)

    def runtime_values(source: str, params: dict[str, str | int]) -> set[str]:
        assert source == "ledger_releases"
        return ledger_releases(ledger, targets, str(params["service"]))

    return Checker(
        keys=keys,
        catalogue=Catalogue.load(CATALOGUE),
        targets=targets,
        runtime_values=runtime_values,
        set_nx=streams.set_nx,
        now=lambda: clock[0],
    )


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


@pytest.fixture
def docker() -> FakeDockerClient:
    """The testbed after scenario 2: api 1.5.0 slot 1 running, worker 2.1.0
    running, redis running; every other slot created and stopped."""
    fake = FakeDockerClient()
    for release, tag in (("1.4.0", "140"), ("1.5.0", "150")):
        for replica in range(1, 6):
            fake.add(f"cs-api-{tag}-{replica}", "api", release, replica)
    fake.add("cs-api-150-1", "api", "1.5.0", 1, state="running", health="healthy")
    fake.add("cs-worker-210", "worker", "2.1.0", 1, state="running", health="healthy")
    fake.add("cs-worker-220", "worker", "2.2.0", 1)
    fake.add("cs-redis", "redis", "7.4.11", 1, state="running", health="healthy")
    # Labelled like an api slot but not a Chaos Shop name: never a target (§7.4).
    fake.add("backend-api", "api", "1.4.0", 1, state="running", health="healthy")
    return fake


@pytest.fixture
def dry_runner(
    docker: FakeDockerClient, ledger: Path, streams: FakeStreams, clock: list[datetime]
) -> DryRunner:
    return DryRunner(
        docker=docker,
        targets=Targets.load(TARGETS),
        ledger_path=ledger,
        counters=streams,
        cache_stats=lambda: dict(CACHE_STATS),
        now=lambda: clock[0],
    )
