"""AI-021: no provider SDK is imported outside ``agent/providers/``."""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "oncallpilot"
SDK_IMPORT = re.compile(r"^\s*(?:import|from)\s+(?:anthropic|google|openai)\b", re.MULTILINE)


def test_provider_sdks_only_in_the_providers_package() -> None:
    offenders = [
        str(path.relative_to(PACKAGE))
        for path in PACKAGE.rglob("*.py")
        if "agent/providers/" not in path.as_posix() and SDK_IMPORT.search(path.read_text())
    ]
    assert offenders == []
