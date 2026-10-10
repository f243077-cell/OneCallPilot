"""C2 — REST request and response bodies (architecture §9.1, §9.2, §9.8; API-001…API-013).

``contracts/openapi.yaml`` lists the endpoints, auth, parameters, and error
responses. It refers to the generated ``schemas/*.json`` of these models, of
the C1 resources (``domain.py``), and of the C8 approval bodies
(``approval.py``), so the Pydantic models stay the single source.

Every error response, on every route, is an ``ErrorEnvelope`` with a code
from §9.8 (API-001). ``details`` carries extra facts, such as ``retry_after``
(seconds) for ``RATE_LIMITED`` and ``COOLDOWN_ACTIVE``.
"""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from oncallpilot_contracts.approval import Reason
from oncallpilot_contracts.common import ContractModel, Semver, UtcDatetime
from oncallpilot_contracts.domain import (
    AuditEntry,
    IncidentSummary,
    LogLine,
    NotifySettings,
    Signal,
    ThresholdName,
)
from oncallpilot_contracts.enums import AlertSource, ErrorCode, ServiceName


class Healthz(ContractModel):
    """``GET /healthz`` (API-002): ``200`` when both are up, ``503`` otherwise."""

    status: Literal["ok", "degraded"]
    contracts_version: Semver
    db: Literal["up", "down"]
    redis: Literal["up", "down"]

    @model_validator(mode="after")
    def _status_matches(self) -> Self:
        healthy = self.db == "up" and self.redis == "up"
        if (self.status == "ok") != healthy:
            raise ValueError("status is ok exactly when db and redis are up")
        return self


class AlertIn(ContractModel):
    """Body of ``POST /ingest/alert`` in the native format (§9.2, API-003)."""

    source: AlertSource
    service: ServiceName
    signals: list[Signal] = Field(min_length=1)
    observed_at: UtcDatetime


class IngestAccepted(ContractModel):
    """``202`` of ``POST /ingest/alert``."""

    incident_id: UUID
    deduplicated: bool


class IncidentPage(ContractModel):
    """``200`` of ``GET /incidents``: newest first (API-004)."""

    items: list[IncidentSummary] = Field(max_length=100)
    next_cursor: str | None = Field(max_length=500, description="Opaque; null on the last page")


class LogTail(ContractModel):
    """``200`` of ``GET /incidents/{id}/log-tail``: redacted lines (API-006)."""

    lines: list[LogLine] = Field(max_length=50)
    until: UtcDatetime = Field(description="Pass as ?since= on the next poll")


class ResolveRequest(ContractModel):
    """Body of ``POST /incidents/{id}/resolve`` (API-007)."""

    reason: Reason


class DeviceRegister(ContractModel):
    """Body of ``POST /devices``: upserted by ``fcm_token`` for the caller (API-011)."""

    fcm_token: str = Field(min_length=1, max_length=4096)
    platform: Literal["android"]
    app_version: str = Field(min_length=1, max_length=50)


class DeviceRegistered(ContractModel):
    """``200`` of ``POST /devices``."""

    device_id: UUID


class AuditPage(ContractModel):
    """``200`` of ``GET /audit``: newest first (API-012)."""

    items: list[AuditEntry] = Field(max_length=100)
    next_cursor: str | None = Field(max_length=500, description="Opaque; null on the last page")


class MonitorSettingsUpdate(ContractModel):
    """Body of ``PUT /settings``; ``version`` is the one last read (FR-023, API-013)."""

    version: int = Field(ge=1)
    services: list[ServiceName] = Field(min_length=1)
    thresholds: dict[ThresholdName, float]
    notify: NotifySettings


class ErrorBody(ContractModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=500)
    details: dict[str, JsonValue]


class ErrorEnvelope(ContractModel):
    """Every error response: ``{"error": {code, message, details}}`` (API-001)."""

    error: ErrorBody
