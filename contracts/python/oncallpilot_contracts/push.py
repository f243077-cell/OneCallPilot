"""C9 — Push payload (architecture §5.8, §9.7; FR-018, MOB-016, SEC-009).

The FCM data message carries exactly the five keys of ``PushPayload``, all
strings, and nothing else: no evidence, no log text, no secrets. Delivery
rules (channel, priority, notification text, deep link) are in
``contracts/push.md``.
"""

from typing import Literal
from uuid import UUID

from pydantic import Field

from oncallpilot_contracts.common import ContractModel
from oncallpilot_contracts.enums import ServiceName, Severity

PushType = Literal[
    "incident.opened",
    "proposal.created",
    "incident.escalated",
    "incident.action_failed",
    "incident.resolved",
]

ANDROID_CHANNEL_ID = "incidents_critical"

PRIORITY: dict[PushType, Literal["high", "normal"]] = {
    "incident.opened": "high",
    "proposal.created": "high",
    "incident.escalated": "high",
    "incident.action_failed": "high",
    "incident.resolved": "normal",
}


class PushPayload(ContractModel):
    """The data part of every FCM message (§9.7)."""

    type: PushType
    incident_id: UUID
    severity: Severity
    service: ServiceName
    title: str = Field(min_length=1, max_length=120)
