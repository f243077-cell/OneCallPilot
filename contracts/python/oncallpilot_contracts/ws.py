"""C3 — WebSocket protocol messages (architecture §5.8, §9.4, §10.4; API-014…API-017, MOB-014).

The protocol itself (connection, auth, heartbeat, resume, ordering, close
codes, client behaviour) is in ``contracts/ws-protocol.md``. One fixture per
message is in ``contracts/fixtures/ws/``.

``WsClientMessage`` is any message the app sends; ``WsServerMessage`` is any
message the server sends. Both are discriminated by ``type``. Events
(``type = "event"``) are further discriminated by ``event``, so each event's
``data`` has its own checked shape.
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, RootModel, StringConstraints, model_validator

from oncallpilot_contracts.common import ContractModel, Semver, UtcDatetime
from oncallpilot_contracts.domain import Evidence, Hypothesis, IncidentSummary, Proposal
from oncallpilot_contracts.enums import (
    EscalationReason,
    IncidentStatus,
    ProposalStatus,
    Resolution,
    Severity,
    StepStatus,
)

WS_PATH = "/ws/incidents"
AUTH_TIMEOUT_SECONDS = 5
PING_INTERVAL_SECONDS = 15
MAX_MISSED_PONGS = 2
CLOSE_UNAUTHORIZED = 4401
CLOSE_AUTH_TIMEOUT = 4408

EventId = Annotated[str, StringConstraints(pattern=r"^[0-9]+-[0-9]+$")]
"""A Redis stream entry ID from ``ocp:events``, e.g. ``1760432425123-0``; monotonic."""


# --- client -> server ---------------------------------------------------------


class WsAuth(ContractModel):
    """First message, within 5 s of connecting; sent again whenever the token refreshes."""

    type: Literal["auth"]
    token: str = Field(min_length=1, max_length=8192)


class WsResume(ContractModel):
    """Optional, after ``hello``: replay every event after ``last_event_id``."""

    type: Literal["resume"]
    last_event_id: EventId


class WsPong(ContractModel):
    """The answer to every ``ping``."""

    type: Literal["pong"]


ClientMessage = Annotated[WsAuth | WsResume | WsPong, Field(discriminator="type")]


class WsClientMessage(RootModel[ClientMessage]):
    """Any message the app sends on ``/ws/incidents``."""


# --- server -> client: control messages ----------------------------------------


class WsHello(ContractModel):
    """Sent once the first ``auth`` succeeds."""

    type: Literal["hello"]
    server_time: UtcDatetime = Field(description="The app uses it for expiry countdowns")
    contracts_version: Semver
    latest_event_id: EventId | None = Field(description="null while ocp:events is empty")


class WsPing(ContractModel):
    """Every 15 s; the app answers ``pong``."""

    type: Literal["ping"]


class WsResyncRequired(ContractModel):
    """``last_event_id`` is older than the retained stream: refetch over REST."""

    type: Literal["resync_required"]


# --- server -> client: event data ------------------------------------------------


class IncidentUpdatedData(ContractModel):
    status: IncidentStatus
    status_reason: EscalationReason | None
    severity: Severity

    @model_validator(mode="after")
    def _reason_when_escalated(self) -> Self:
        if self.status == "escalated" and self.status_reason is None:
            raise ValueError("an escalated incident needs a status_reason")
        return self


class EvidenceAddedData(Evidence):
    """An evidence item; ``payload`` is cut to 4 KB when needed, with ``payload_truncated``."""

    payload_truncated: bool


class HypothesisUpdatedData(ContractModel):
    """The full current set of hypotheses."""

    hypotheses: list[Hypothesis]


class ProposalUpdatedData(ContractModel):
    proposal_id: UUID
    status: ProposalStatus
    status_reason: str | None = Field(max_length=300)
    superseded_by: UUID | None

    @model_validator(mode="after")
    def _superseded_by_when_superseded(self) -> Self:
        if (self.status == "superseded") != (self.superseded_by is not None):
            raise ValueError("superseded_by is set exactly when the status is superseded")
        return self


class ExecutionProgressData(ContractModel):
    execution_id: UUID
    proposal_id: UUID
    step: str = Field(min_length=1, max_length=100)
    status: StepStatus
    message: str = Field(max_length=300)


class IncidentResolvedData(ContractModel):
    resolution: Resolution
    resolved_at: UtcDatetime


# --- server -> client: event envelopes -------------------------------------------


class EventEnvelopeBase(ContractModel):
    """Fields shared by every event (§9.4)."""

    type: Literal["event"]
    event_id: EventId
    incident_id: UUID
    state_version: int = Field(ge=1, description="The incident's state_version after the change")
    ts: UtcDatetime


class IncidentOpenedEvent(EventEnvelopeBase):
    event: Literal["incident.opened"]
    data: IncidentSummary

    @model_validator(mode="after")
    def _same_incident(self) -> Self:
        if self.data.id != self.incident_id or self.data.state_version != self.state_version:
            raise ValueError("data must be this incident at this state_version")
        return self


class IncidentUpdatedEvent(EventEnvelopeBase):
    event: Literal["incident.updated"]
    data: IncidentUpdatedData


class EvidenceAddedEvent(EventEnvelopeBase):
    event: Literal["evidence.added"]
    data: EvidenceAddedData


class HypothesisUpdatedEvent(EventEnvelopeBase):
    event: Literal["hypothesis.updated"]
    data: HypothesisUpdatedData


class ProposalCreatedEvent(EventEnvelopeBase):
    event: Literal["proposal.created"]
    data: Proposal

    @model_validator(mode="after")
    def _same_incident(self) -> Self:
        if self.data.incident_id != self.incident_id:
            raise ValueError("the proposal must belong to this incident")
        return self


class ProposalUpdatedEvent(EventEnvelopeBase):
    event: Literal["proposal.updated"]
    data: ProposalUpdatedData


class ExecutionProgressEvent(EventEnvelopeBase):
    event: Literal["execution.progress"]
    data: ExecutionProgressData


class IncidentResolvedEvent(EventEnvelopeBase):
    event: Literal["incident.resolved"]
    data: IncidentResolvedData


WsEvent = Annotated[
    IncidentOpenedEvent
    | IncidentUpdatedEvent
    | EvidenceAddedEvent
    | HypothesisUpdatedEvent
    | ProposalCreatedEvent
    | ProposalUpdatedEvent
    | ExecutionProgressEvent
    | IncidentResolvedEvent,
    Field(discriminator="event"),
]
"""Any of the 8 events of §9.4."""

ServerMessage = Annotated[
    WsHello | WsPing | WsResyncRequired | WsEvent, Field(discriminator="type")
]


class WsServerMessage(RootModel[ServerMessage]):
    """Any message the server sends on ``/ws/incidents``."""
