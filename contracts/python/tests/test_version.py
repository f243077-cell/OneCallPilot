import tomllib
from pathlib import Path

CONTRACTS_DIR = Path(__file__).resolve().parents[2]


def test_package_version_matches_contracts_version() -> None:
    pyproject = tomllib.loads((CONTRACTS_DIR / "python" / "pyproject.toml").read_text("utf-8"))
    assert pyproject["project"]["version"] == (CONTRACTS_DIR / "VERSION").read_text("utf-8").strip()
