"""Read access to the deploy ledger (C6, LEDGER_PATH).

- `ledger_releases(service)` gives the values of the catalogue's
  `values_from: ledger_releases`: releases of the service in the ledger history
  that have slot containers (runner/targets.yaml), excluding the active one
  (the release of the service's latest record).
- `active(service)` is the service's latest record: its release and desired
  replica count, the two ledger values of the state fingerprint (§7.5). A
  service with no record (redis) has neither.

Writing records comes with the handlers (A2.4).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oncallpilot_runner.targets import Targets


class LedgerError(ValueError):
    """The ledger cannot be read: whatever depends on it is refused."""


@dataclass(frozen=True)
class Active:
    release: str | None
    replicas: int | None


def _records(path: Path, service: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError as exc:
        raise LedgerError(f"cannot read the deploy ledger: {exc}") from exc
    records: list[dict[str, Any]] = []
    for line in lines[:-1]:  # a last line without a newline is a write in progress
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise LedgerError("the deploy ledger has a line that is not JSON") from exc
        if isinstance(record, dict) and record.get("service") == service:
            records.append(record)
    return records


def ledger_releases(path: Path, targets: Targets, service: str) -> set[str]:
    history = [str(record.get("release")) for record in _records(path, service)]
    if not history:
        return set()
    spec = targets.services.get(service)
    slots = set(spec.releases) if spec else set()
    return (set(history) & slots) - {history[-1]}


def active(path: Path, service: str) -> Active:
    records = _records(path, service)
    if not records:
        return Active(None, None)
    latest = records[-1]
    release, replicas = latest.get("release"), latest.get("replicas")
    if not isinstance(release, str) or not isinstance(replicas, int) or isinstance(replicas, bool):
        raise LedgerError(f"the latest {service} record has no valid release and replicas")
    return Active(release, replicas)
