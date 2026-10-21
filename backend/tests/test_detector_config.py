"""detector_config_version (ADR-20)."""

from pathlib import Path

from oncallpilot.detector.config import config_version, read_config

CONFIG = Path(__file__).resolve().parents[1] / "config" / "detector.yaml"


def test_version_changes_with_the_file_and_the_overrides() -> None:
    base = config_version(b"a: 1\n")
    assert base == config_version(b"a: 1\n", {})
    assert len(base) == 64
    assert config_version(b"a: 2\n") != base
    assert config_version(b"a: 1\n", {"error_rate.z": 5.0}) != base
    assert config_version(b"a: 1\n", {"x": 1.0, "y": 2.0}) == config_version(
        b"a: 1\n", {"y": 2.0, "x": 1.0}
    )


def test_the_shipped_config_reads() -> None:
    assert b"error_rate" in read_config(CONFIG)
