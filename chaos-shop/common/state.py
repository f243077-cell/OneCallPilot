"""Small JSON state files in the container's writable layer (``CS_STATE_DIR``).

They survive a container restart (crash, OOM kill, restart policy, `docker restart`)
but not a re-create, so a fault stays until it is fixed or `chaos reset` clears it
(ADR-19). Never put secrets here.
"""

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = "/var/lib/shop"


def state_dir() -> Path:
    return Path(os.environ.get("CS_STATE_DIR", DEFAULT_STATE_DIR))


def load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temporary, path)
