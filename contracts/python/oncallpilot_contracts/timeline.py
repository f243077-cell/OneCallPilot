"""Phase 0 fixture timelines (phases.md §3, S0.4; architecture §10.6; MOB-017).

A timeline is one incident from start to end as the app sees it: ordered
WebSocket events (C3) with delays, plus the final ``IncidentDetail`` (C1)
that ``GET /incidents/{id}`` returns afterwards. The files are in
``contracts/fixtures/timelines/``.

Users
    - **The app's mock mode** (``MockIncidentRepository``) waits
      ``delay_ms`` before each step and then emits its event. At a step with
      ``await_approval: true`` it first waits until the user approves the
      pending proposal (challenge → local_auth → approve, simulated), then
      continues. ``GET /incidents/{id}`` can be answered from the events so
      far, or from ``final`` at the end.
    - **``ocp seed-incident <timeline>``** writes ``final`` to the database
      and publishes the events to ``ocp:events``, for live WebSocket tests.
    - **backend gateway tests** replay the same events.

Rules (checked here and by the tests)
    - Every event belongs to ``incident_id``, and the first one is
      ``incident.opened``.
    - ``event_id`` and ``state_version`` rise with every event, and
      ``final`` carries the last ``state_version``.
    - ``final`` agrees with the events: the same evidence refs, the last
      hypothesis set, each proposal's last status, and the resolution.
"""

from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from oncallpilot_contracts.common import ContractModel
from oncallpilot_contracts.domain import IncidentDetail
from oncallpilot_contracts.ws import WsEvent


class TimelineStep(ContractModel):
    delay_ms: int = Field(ge=0, le=60_000, description="Wait before emitting this event")
    await_approval: bool = Field(
        default=False, description="Mock mode: wait for the user's approval before this step"
    )
    event: WsEvent


def _stream_position(event_id: str) -> tuple[int, int]:
    milliseconds, sequence = event_id.split("-")
    return int(milliseconds), int(sequence)


class Timeline(ContractModel):
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    description: str = Field(min_length=1, max_length=500)
    incident_id: UUID
    steps: list[TimelineStep] = Field(min_length=1)
    final: IncidentDetail

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        events = [step.event for step in self.steps]
        if events[0].event != "incident.opened":
            raise ValueError("a timeline starts with incident.opened")
        if any(event.incident_id != self.incident_id for event in events):
            raise ValueError("every event belongs to incident_id")
        if self.final.id != self.incident_id:
            raise ValueError("final is the same incident")
        for before, after in zip(events, events[1:], strict=False):
            if after.state_version <= before.state_version:
                raise ValueError(f"state_version must rise at {after.event_id}")
            if _stream_position(after.event_id) <= _stream_position(before.event_id):
                raise ValueError(f"event_id must rise at {after.event_id}")
        if self.final.state_version != events[-1].state_version:
            raise ValueError("final carries the last state_version")
        return self
