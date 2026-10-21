"""Read the deploy ledger (C6, ADR-12): ``deploys.jsonl`` on a read-only mount.

Readers ignore a trailing line without ``\\n`` (a write in progress) and
never write. Used by the detector (``kind=reset`` starts the warm-up) and,
later, ``get_recent_deploys``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from oncallpilot_contracts.ledger import DeployRecord
from pydantic import ValidationError

log = logging.getLogger(__name__)


def read_records(path: Path) -> list[DeployRecord]:
    """Every complete, valid record in file order; a missing file reads as empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    complete = text if text.endswith("\n") else text[: text.rfind("\n") + 1]
    records: list[DeployRecord] = []
    for number, line in enumerate(complete.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(DeployRecord.model_validate_json(line))
        except ValidationError:
            log.warning("deploy ledger line %d is not a valid record; skipped", number)
    return records


def latest_reset(records: list[DeployRecord]) -> DeployRecord | None:
    resets = [r for r in records if r.kind == "reset"]
    return max(resets, key=lambda r: r.deployed_at) if resets else None
