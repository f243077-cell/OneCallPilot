"""Read access to the deploy ledger (C6, LEDGER_PATH) for parameter checks.

`ledger_releases(service)` gives the values of the catalogue's
`values_from: ledger_releases`: releases of the service in the ledger history
that have slot containers (runner/targets.yaml), excluding the active one
(the release of the service's latest record). Writing records comes with the
handlers (A2.4).
"""

import json
from pathlib import Path

from oncallpilot_runner.targets import Targets


class LedgerError(ValueError):
    """The ledger cannot be read: a parameter that depends on it is refused."""


def ledger_releases(path: Path, targets: Targets, service: str) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError as exc:
        raise LedgerError(f"cannot read the deploy ledger: {exc}") from exc
    history: list[str] = []
    for line in lines[:-1]:  # a last line without a newline is a write in progress
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise LedgerError("the deploy ledger has a line that is not JSON") from exc
        if isinstance(record, dict) and record.get("service") == service:
            history.append(str(record.get("release")))
    if not history:
        return set()
    spec = targets.services.get(service)
    slots = set(spec.releases) if spec else set()
    return (set(history) & slots) - {history[-1]}
