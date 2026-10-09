"""C6 — Deploy ledger record (architecture §9.6, ADR-12; TB-009, TB-010).

File
    ``deploys.jsonl`` at the path in ``LEDGER_PATH``, on a shared volume.
    Writers: the chaos CLI and the runner (read-write). Reader: backend-worker
    (read-only mount), for ``get_recent_deploys`` and the state fingerprint.

Format
    One ``DeployRecord`` as compact JSON per line, UTF-8, ``\\n`` line endings.
    Writers append a whole line with one write and flush; readers ignore a
    trailing line without ``\\n`` (a write in progress). The file is
    append-only, except that ``chaos reset`` replaces it atomically (write a
    temporary file, then rename) with the seeded history.

State
    Each record describes the service's state **after** the event: the
    active ``release`` and the desired ``replicas``. The latest record for a
    service is its current state. Records of other services never change it.

Kinds and writers
    ======== ============================ =====================================
    kind     deployed_by                  written when
    ======== ============================ =====================================
    deploy   ``ci`` or ``setup``          a fault deploy (chaos CLI, scenarios
                                          2 and 6) or a seeded-history entry
    rollback ``runner`` or ``setup``      the runner executed ``rollback_deploy``
    scale    ``runner`` or ``setup``      the runner executed ``scale_service``
    reset    ``setup``                    ``chaos reset``: one record per
                                          service (``api`` and ``worker``),
                                          after the seeded history
    ======== ============================ =====================================

    The detector starts its warm-up when a new ``reset`` record appears.

Seeded history
    ``chaos-shop/releases.yaml`` lists, per service, the release history to
    write on reset (each entry gives every field below except ``deploy_id``,
    which is generated, ``kind`` = ``deploy`` and ``deployed_by`` = ``setup``).

Neutral naming (TB-010)
    ``release``, ``commit_message``, ``deployed_by`` and ``reason`` are visible
    to the agent and must never name a fault or the fault injector.
"""

from datetime import datetime, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

DeployKind = Literal["deploy", "rollback", "scale", "reset"]
DeployedBy = Literal["ci", "runner", "setup"]
LedgerService = Literal["api", "worker"]

ALLOWED_WRITERS: dict[str, frozenset[str]] = {
    "deploy": frozenset({"ci", "setup"}),
    "rollback": frozenset({"runner", "setup"}),
    "scale": frozenset({"runner", "setup"}),
    "reset": frozenset({"setup"}),
}

SEMVER = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"


class DeployRecord(BaseModel):
    """One line of ``deploys.jsonl``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    deploy_id: UUID
    kind: DeployKind
    service: LedgerService
    release: str = Field(pattern=SEMVER)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    commit_message: str = Field(min_length=1, max_length=200)
    image_tag: str = Field(pattern=r"^chaos-shop/(api|worker):[0-9]+\.[0-9]+\.[0-9]+$")
    config_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$", description="SHA-256 hex of the effective config"
    )
    replicas: int = Field(ge=1, le=5)
    deployed_at: AwareDatetime = Field(description="UTC, serialised with a Z suffix")
    deployed_by: DeployedBy
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("deployed_at")
    @classmethod
    def _utc_only(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("deployed_at must be UTC")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.deployed_by not in ALLOWED_WRITERS[self.kind]:
            raise ValueError(f"deployed_by={self.deployed_by!r} cannot write kind={self.kind!r}")
        if self.image_tag != f"chaos-shop/{self.service}:{self.release}":
            raise ValueError("image_tag must be chaos-shop/<service>:<release>")
        if self.service == "worker" and self.replicas != 1:
            raise ValueError("worker runs exactly one replica")
        return self
