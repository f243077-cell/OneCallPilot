"""Read access to the deploy ledger (C6, LEDGER_PATH).

- `ledger_releases(service)` gives the values of the catalogue's
  `values_from: ledger_releases`: releases of the service in the ledger history
  that have slot containers (runner/targets.yaml), excluding the active one
  (the release of the service's latest record).
- `active(service)` is the service's latest record: its release and desired
  replica count, the two ledger values of the state fingerprint (§7.5). A
  service with no record (redis) has neither.
- `runner_record(...)` builds the C6 record the runner appends after a
  rollback (and, in A2.4b, a scale). The release's commit, message, image tag
  and config hash are copied from that release's own latest record: the runner
  has no copy of chaos-shop/releases.yaml, and a release can only be a rollback
  target if it is in the ledger history (`ledger_releases`).
- `append(record)`: C6's writer rule. One whole line in one `write()` on a
  descriptor opened with O_APPEND, then fsync. The file is never created (no
  O_CREAT) and never replaced: only `chaos reset` replaces it. The chaos CLI's
  helper appends the same way, so whole lines never interleave.
"""

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
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


# C6 DeployRecord rules the runner must meet (contracts/python/.../ledger.py).
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
COPIED = ("commit_sha", "commit_message", "image_tag", "config_hash")


def runner_record(
    path: Path, *, kind: str, service: str, release: str, replicas: int, reason: str,
    now: datetime,
) -> dict[str, Any]:  # fmt: skip
    """A valid C6 record with deployed_by=runner, or LedgerError."""
    source = [r for r in _records(path, service) if r.get("release") == release]
    if not source:
        raise LedgerError(f"{service} {release} has no record to copy its details from")
    latest = source[-1]
    record: dict[str, Any] = {
        "deploy_id": str(uuid.uuid4()),
        "kind": kind,
        "service": service,
        "release": release,
        **{field: latest.get(field) for field in COPIED},
        "replicas": replicas,
        "deployed_at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployed_by": "runner",
        "reason": reason,
    }
    problems = _problems(record)
    if problems:
        raise LedgerError(f"the {kind} record would be invalid: {'; '.join(problems)}")
    return record


def _problems(r: dict[str, Any]) -> list[str]:
    checks = {
        "kind is rollback or scale": r["kind"] in ("rollback", "scale"),
        "service is api or worker": r["service"] in ("api", "worker"),
        "release is semver": isinstance(r["release"], str)
        and bool(_SEMVER.fullmatch(r["release"])),
        "commit_sha is 40 hex": isinstance(r["commit_sha"], str)
        and bool(_SHA1.fullmatch(r["commit_sha"])),
        "commit_message is 1-200 characters": isinstance(r["commit_message"], str)
        and 1 <= len(r["commit_message"]) <= 200,
        "image_tag is chaos-shop/<service>:<release>": r["image_tag"]
        == f"chaos-shop/{r['service']}:{r['release']}",
        "config_hash is 64 hex": isinstance(r["config_hash"], str)
        and bool(_SHA256.fullmatch(r["config_hash"])),
        "replicas is 1-5 (worker: 1)": isinstance(r["replicas"], int)
        and 1 <= r["replicas"] <= (1 if r["service"] == "worker" else 5),
        "reason is 1-200 characters": 1 <= len(r["reason"]) <= 200,
    }
    return [rule for rule, ok in checks.items() if not ok]


def append(path: Path, record: dict[str, Any]) -> None:
    line = (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    except OSError as exc:
        raise LedgerError(f"cannot open the deploy ledger for appending: {exc}") from exc
    try:
        written = os.write(fd, line)
        os.fsync(fd)
    except OSError as exc:
        raise LedgerError(f"cannot append to the deploy ledger: {exc}") from exc
    finally:
        os.close(fd)
    if written != len(line):
        raise LedgerError("the deploy ledger took only part of the record")
