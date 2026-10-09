"""Live checks of the chaos CLI against the running testbed (TB-006..TB-010).

Run from chaos-shop/cli on the integration host, with the testbed up:

    uv run pytest -m live -v                       # everything, about 45 minutes
    uv run pytest -m live -v -k "not verify"       # reset, inject, ledger: about 4 minutes
    OCP_VERIFY_HOLD=60 uv run pytest -m live -k verify   # shorter holds while developing

They change the testbed and always finish with `chaos reset`.
"""

import os
import re
import subprocess
import sys
import time

import pytest
from oncallpilot_contracts.ledger import ALLOWED_WRITERS

from chaos_cli.config import REPO_ROOT, load_secrets
from chaos_cli.docker_ops import Docker
from chaos_cli.ledger import Releases
from chaos_cli.testbed import SCENARIOS, Testbed

pytestmark = pytest.mark.live

BANNED = re.compile(r"bad|leak|broken|chaos|fault|inject", re.IGNORECASE)
HOLD = os.environ.get("OCP_VERIFY_HOLD", "180")


def chaos(*args: str, timeout: float = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "chaos_cli", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def testbed() -> Testbed:
    return Testbed(Docker(), load_secrets(), Releases.load())


def test_reset_within_90_seconds_and_status_shows_the_baseline(testbed: Testbed) -> None:
    started = time.monotonic()
    result = chaos("reset")
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started <= 90
    snap = testbed.snapshot()
    assert snap.baseline, snap.problems
    # TB-002: status lists every slot with its labels.
    listing = chaos("status").stdout
    for name in ("cs-api-150-5", "cs-worker-220", "cs-api-140-1"):
        assert re.search(
            rf"^{name}\s+\S+\s+\S+\s+(api|worker)\s+\d+\.\d+\.\d+\s+\d$", listing, re.M
        )


@pytest.mark.parametrize("number", sorted(SCENARIOS))
def test_inject_within_30_seconds_and_status_shows_it(testbed: Testbed, number: int) -> None:
    assert chaos("reset").returncode == 0
    try:
        started = time.monotonic()
        result = chaos("inject", str(number))
        seconds = time.monotonic() - started
        assert result.returncode == 0, result.stdout + result.stderr
        assert seconds <= 30, f"inject {number} took {seconds:.1f} s"
        assert not testbed.snapshot().baseline
    finally:
        assert chaos("reset").returncode == 0


def test_live_ledger_is_valid_and_neutral(testbed: Testbed) -> None:
    assert chaos("reset").returncode == 0
    try:
        assert chaos("inject", "2").returncode == 0
        assert chaos("inject", "6").returncode == 0
        records = testbed.read_ledger()
        assert [(r.kind, r.service, r.deployed_by) for r in records[-2:]] == [
            ("deploy", "api", "ci"),
            ("deploy", "worker", "ci"),
        ]
        for record in records:
            assert record.deployed_by in ALLOWED_WRITERS[record.kind]
            visible = record.model_dump(mode="json")
            visible.pop("image_tag")  # fixed by C6, never returned to the agent
            visible.pop("deploy_id")  # random hex; can spell "bad" by chance
            assert not [v for v in visible.values() if BANNED.search(str(v))]
    finally:
        assert chaos("reset").returncode == 0


@pytest.mark.parametrize("number", sorted(SCENARIOS))
def test_verify(number: int) -> None:
    """TB-008: broken after the hold; 1-6 recover after the fix within 120 s."""
    result = chaos("verify", str(number), "--hold", HOLD)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
