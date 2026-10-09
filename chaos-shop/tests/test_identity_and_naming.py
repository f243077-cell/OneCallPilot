"""Release identity (C7 app_info) and neutral naming of testbed names (TB-010, part)."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from common.identity import ConfigError, commit_for, load_identity

SHOP = Path(__file__).resolve().parents[1]
RELEASES = SHOP / "releases.yaml"
TESTBED = SHOP.parent / "infrastructure" / "compose" / "testbed.yml"
# Words that would name a fault or the injector (C7 §1, TB-010).
BANNED = re.compile(r"bad|leak|broken|chaos|fault|inject", re.IGNORECASE)
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_commit_lookup() -> None:
    assert commit_for(RELEASES, "api", "1.4.0") == "606c2674b8e3"
    assert commit_for(RELEASES, "worker", "2.1.0") == "69bbb70d749c"
    with pytest.raises(ConfigError):
        commit_for(RELEASES, "api", "9.9.9")


def test_identity_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CS_RELEASE", "1.4.0")
    monkeypatch.setenv("CS_INSTANCE", "cs-api-140-3")
    monkeypatch.setenv("CS_RELEASES_FILE", str(RELEASES))
    identity = load_identity("api", with_commit=True)
    assert (identity.release, identity.instance, identity.commit) == (
        "1.4.0",
        "cs-api-140-3",
        "606c2674b8e3",
    )
    monkeypatch.delenv("CS_INSTANCE")
    with pytest.raises(ConfigError):
        load_identity("api", with_commit=False)


def test_releases_are_semver_with_neutral_messages() -> None:
    for service, releases in load(RELEASES)["releases"].items():
        assert service in ("api", "worker")
        for release, entry in releases.items():
            assert SEMVER.fullmatch(release)
            assert re.fullmatch(r"[0-9a-f]{40}", entry["commit_sha"])
            assert 1 <= len(entry["commit_message"]) <= 200
            assert not BANNED.search(entry["commit_message"]), entry["commit_message"]


def test_testbed_containers_labels_and_aliases_are_neutral() -> None:
    services = load(TESTBED)["services"]
    for key, service in services.items():
        name = service["container_name"]
        assert name == key and name.startswith("cs-")
        assert not BANNED.search(name), name
        labels = service.get("labels", {})
        for value in labels.values():
            assert not BANNED.search(str(value)), (name, value)
        networks = service.get("networks", {})
        if isinstance(networks, dict):
            for config in networks.values():
                for alias in (config or {}).get("aliases", []):
                    assert not BANNED.search(alias), alias


def test_managed_containers_carry_all_five_labels() -> None:
    services = load(TESTBED)["services"]
    for key, service in services.items():
        labels = service.get("labels", {})
        if key == "cs-loadgen":
            assert not labels, "cs-loadgen must carry no oncallpilot.* labels (C7 §1)"
            continue
        assert set(labels) == {
            "oncallpilot.managed",
            "oncallpilot.service",
            "oncallpilot.release",
            "oncallpilot.replica",
            "oncallpilot.logs",
        }, key
        assert labels["oncallpilot.managed"] == "true"
        assert labels["oncallpilot.logs"] == "true"
        build = service.get("build")
        if build is not None:  # a Chaos Shop image: the label, tag and build arg agree
            release = labels["oncallpilot.release"]
            assert build["args"]["CS_RELEASE"] == release
            assert service["image"].endswith(f":{release}")
            assert service["environment"]["CS_INSTANCE"] == key
