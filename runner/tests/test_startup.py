"""The runner refuses to start without its keys, catalogue and allowlist (fail closed)."""

import pytest

from oncallpilot_runner import __main__ as entry
from tests.conftest import CATALOGUE, TARGETS

GOOD = {
    "ACTION_SIGNING_KEY": "aa" * 32,
    "RUNNER_LINK_KEY": "bb" * 32,
    "RUNNER_REDIS_URL": "redis://x",
}


@pytest.mark.parametrize(
    "env",
    [
        {**GOOD, "ACTION_SIGNING_KEY": ""},
        {**GOOD, "RUNNER_LINK_KEY": "bb" * 16},
        {**GOOD, "RUNNER_LINK_KEY": "aa" * 32},  # the same key twice
        {k: v for k, v in GOOD.items() if k != "RUNNER_REDIS_URL"},
    ],
)
def test_missing_or_bad_settings_stop_the_runner(
    env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in GOOD:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as stop:
        entry.main(["--catalogue", str(CATALOGUE), "--targets", str(TARGETS)])
    assert stop.value.code == 2


def test_a_missing_catalogue_stops_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in GOOD.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as stop:
        entry.main(["--catalogue", "missing.yaml", "--targets", str(TARGETS)])
    assert stop.value.code == 2
