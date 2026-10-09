"""Deploy ledger records written by the chaos CLI (C6 ``DeployRecord``, TB-009, TB-010).

Every record is built and validated with the shared contract model before it is
written. Writers: ``chaos inject`` records its fault deploys as ``deployed_by="ci"``
(so no agent-visible field names the injector), seeded history and ``chaos reset``
use ``setup``, and ``chaos verify`` writes its direct fixes as ``setup``.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from oncallpilot_contracts.ledger import DeployRecord

from chaos_cli.config import RELEASES_FILE

Service = Literal["api", "worker"]
Kind = Literal["deploy", "rollback", "scale", "reset"]
Writer = Literal["ci", "runner", "setup"]

REASON_RELEASE = "scheduled release"
REASON_RESET = "environment restored to baseline"
REASON_ROLLBACK = "rolled back to the previous release"
REASON_SCALE = "capacity change"


def config_hash(config: Mapping[str, str]) -> str:
    """SHA-256 over the release configuration as compact JSON with sorted keys."""
    canonical = json.dumps(dict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Releases:
    releases: dict[str, dict[str, dict[str, Any]]]
    baseline: dict[str, str]
    history: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path = RELEASES_FILE) -> "Releases":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls(releases=data["releases"], baseline=data["baseline"], history=data["history"])

    def entry(self, service: str, release: str) -> dict[str, Any]:
        return self.releases[service][release]


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_line(record: DeployRecord) -> str:
    """One compact JSON line (C6 format), ``deployed_at`` with a Z suffix."""
    data = record.model_dump(mode="json")
    data["deployed_at"] = record.deployed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps(data, separators=(",", ":"))


def make_record(
    releases: Releases,
    *,
    kind: Kind,
    service: Service,
    release: str,
    replicas: int,
    deployed_by: Writer,
    reason: str,
    now: Callable[[], datetime] = utc_now,
) -> DeployRecord:
    entry = releases.entry(service, release)
    return DeployRecord(
        deploy_id=uuid4(),
        kind=kind,
        service=service,
        release=release,
        commit_sha=entry["commit_sha"],
        commit_message=entry["commit_message"],
        image_tag=f"chaos-shop/{service}:{release}",
        config_hash=config_hash(entry["config"]),
        replicas=replicas,
        deployed_at=now().replace(microsecond=0),
        deployed_by=deployed_by,
        reason=reason,
    )


def seeded_ledger(releases: Releases, now: Callable[[], datetime] = utc_now) -> list[DeployRecord]:
    """The seeded history, then one ``reset`` record per service (C6 docstring)."""
    records = [
        DeployRecord(
            deploy_id=uuid4(),
            kind="deploy",
            deployed_by="setup",
            **{**entry, "deployed_at": datetime.fromisoformat(entry["deployed_at"])},
        )
        for entry in releases.history
    ]
    for service in ("api", "worker"):
        records.append(
            make_record(
                releases,
                kind="reset",
                service=service,
                release=releases.baseline[service],
                replicas=1,
                deployed_by="setup",
                reason=REASON_RESET,
                now=now,
            )
        )
    return records


def parse_lines(text: str) -> list[DeployRecord]:
    """Complete lines only: a trailing line without a newline is a write in progress."""
    complete = text.split("\n")[:-1]
    return [DeployRecord.model_validate_json(line) for line in complete if line.strip()]


def current(records: list[DeployRecord], service: str) -> DeployRecord | None:
    """The latest record for a service is its current state (C6)."""
    for record in reversed(records):
        if record.service == service:
            return record
    return None
