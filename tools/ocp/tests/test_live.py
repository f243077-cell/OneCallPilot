"""ocp against the real Docker stack (Gate G1 criterion 1, SEC-013).

Run from tools/ocp on the integration host, with Docker running:

    uv run pytest -m live -v        # about 1 minute; leaves the stack up at baseline
"""

import subprocess
import sys

import pytest

from ocp_cli.stack import Stack

pytestmark = pytest.mark.live


def ocp(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from ocp_cli.main import main; main()", *args],
        cwd=Stack.discover().root,
        capture_output=True,
        text=True,
        timeout=600,
    )


def test_up_brings_every_service_to_healthy() -> None:
    result = ocp("up", "--no-build")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "services healthy" in result.stdout
    assert "API_BASE_URL=http://" in result.stdout


def test_doctor_passes_on_the_running_stack() -> None:
    result = ocp("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
