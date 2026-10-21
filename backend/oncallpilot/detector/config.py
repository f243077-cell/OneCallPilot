"""``detector_config_version`` (architecture §6.8, ADR-20).

The SHA-256 of the detector config file's bytes plus the threshold overrides
from ``monitor_settings``, so any change to either gives a new version that is
stamped on every incident opened afterwards.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_PATH = Path("config") / "detector.yaml"


def config_version(config: bytes, overrides: dict[str, float] | None = None) -> str:
    digest = hashlib.sha256(config)
    digest.update(b"\0")
    digest.update(json.dumps(overrides or {}, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()


def read_config(path: Path = DEFAULT_PATH) -> bytes:
    return path.read_bytes()
