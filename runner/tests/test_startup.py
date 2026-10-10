"""The runner refuses to start without its keys, catalogue, allowlist, Docker
settings or admin token (fail closed), and never with a Docker socket path."""

import pytest

from oncallpilot_runner import __main__ as entry
from oncallpilot_runner.consumer import Consumer
from oncallpilot_runner.docker_client import SdkDockerReader
from tests.conftest import CATALOGUE, TARGETS

GOOD = {
    "ACTION_SIGNING_KEY": "aa" * 32,
    "RUNNER_LINK_KEY": "bb" * 32,
    "RUNNER_REDIS_URL": "redis://x",
    "DOCKER_HOST": "tcp://socket-proxy-rw:2375",
    "DOCKER_API_VERSION": "1.48",
    "RUNNER_ADMIN_TOKEN": "cc" * 24,
}


@pytest.mark.parametrize(
    "env",
    [
        {**GOOD, "ACTION_SIGNING_KEY": ""},
        {**GOOD, "RUNNER_LINK_KEY": "bb" * 16},
        {**GOOD, "RUNNER_LINK_KEY": "aa" * 32},  # the same key twice
        {k: v for k, v in GOOD.items() if k != "RUNNER_REDIS_URL"},
        {**GOOD, "DOCKER_HOST": "unix:///var/run/docker.sock"},  # never the socket
        {**GOOD, "DOCKER_HOST": "npipe:////./pipe/docker_engine"},
        {k: v for k, v in GOOD.items() if k != "DOCKER_HOST"},
        {**GOOD, "DOCKER_API_VERSION": "auto"},  # must be pinned: the proxy blocks /version
        {k: v for k, v in GOOD.items() if k != "DOCKER_API_VERSION"},
        {**GOOD, "RUNNER_ADMIN_TOKEN": "short"},
        {k: v for k, v in GOOD.items() if k != "RUNNER_ADMIN_TOKEN"},
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


def test_good_settings_build_a_consumer_with_a_dry_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """With every setting the runner starts consuming, with a dry runner that
    talks to the proxy (the client is built lazily: no Docker call at start)."""
    for name, value in GOOD.items():
        monkeypatch.setenv(name, value)
    started: list[Consumer] = []

    class Stop(Exception):
        pass

    def fake_run_forever(self: Consumer) -> None:
        started.append(self)
        raise Stop

    monkeypatch.setattr(Consumer, "run_forever", fake_run_forever)
    with pytest.raises(Stop):
        entry.main(["--catalogue", str(CATALOGUE), "--targets", str(TARGETS)])
    (consumer,) = started
    assert consumer.dry_runner is not None
    reader = consumer.dry_runner.docker
    assert isinstance(reader, SdkDockerReader)
    assert reader.client.api.base_url == "http://socket-proxy-rw:2375"
    assert reader.client.api._version == "1.48"
