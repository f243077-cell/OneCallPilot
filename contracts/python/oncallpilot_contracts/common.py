"""Shared building blocks for the contract models.

Every model inherits ``ContractModel``: unknown fields are rejected
(``extra="forbid"``) and instances are immutable (``frozen=True``), as
``CLAUDE.md`` §4.4 requires for contract models.

Timestamps are ``UtcDatetime``: timezone-aware, UTC only, serialised as
ISO 8601 with a ``Z`` suffix (architecture §9.1).
"""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, StringConstraints


class ContractModel(BaseModel):
    """Base class of every contract model."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be UTC")
    return value


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]
"""Timezone-aware UTC timestamp, serialised with a ``Z`` suffix."""

SEMVER_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"

Semver = Annotated[str, StringConstraints(pattern=SEMVER_PATTERN)]
"""A plain ``MAJOR.MINOR.PATCH`` version, such as a release or ``catalogue_version``."""

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""SHA-256 digest as 64 lower-case hex characters (fingerprints, config hashes)."""

EvidenceRef = Annotated[str, StringConstraints(pattern=r"^E[0-9]{1,3}$")]
"""Short evidence reference shown to the model and the user: ``E1``, ``E2``, … (§6.4)."""

ContainerName = Annotated[str, StringConstraints(pattern=r"^cs-[a-z0-9]+(-[a-z0-9]+)*$")]
"""Name of a Chaos Shop container, e.g. ``cs-api-140-1`` (C7 §1)."""
