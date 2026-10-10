"""C8 — Approval protocol bodies (architecture §5.9, §7.5, §7.6; API-008…API-010, SEC-012, MOB-008).

The protocol (steps, the order of the server's checks, error codes, the
Idempotency-Key rules, and what the app must do) is in
``contracts/approval-protocol.md``. The endpoints are in ``openapi.yaml`` (C2).
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import StringConstraints

from oncallpilot_contracts.common import ContractModel, Sha256Hex, UtcDatetime
from oncallpilot_contracts.enums import ApprovalRequirement, AuthMethod

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
CHALLENGE_TTL_SECONDS = 120

Nonce = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""32 random bytes as 64 lower-case hex characters. The server stores only its SHA-256."""

Reason = Annotated[str, StringConstraints(min_length=3, max_length=500)]
"""A human-written reason (reject, manual resolve)."""


class ChallengeRequest(ContractModel):
    """Body of ``POST /proposals/{id}/challenge``."""

    proposal_fingerprint: Sha256Hex


class ChallengeResponse(ContractModel):
    """``200`` of ``POST /proposals/{id}/challenge``: single use, valid for 120 s."""

    challenge_id: UUID
    nonce: Nonce
    expires_at: UtcDatetime
    approval_requirement: ApprovalRequirement


class ApproveRequest(ContractModel):
    """Body of ``POST /proposals/{id}/approve`` (header ``Idempotency-Key`` required).

    ``device_id`` is optional: the app always sends it; API clients such as
    ``ocp smoke`` may leave it out.
    """

    challenge_id: UUID
    nonce: Nonce
    proposal_fingerprint: Sha256Hex
    auth_method: AuthMethod
    device_id: UUID | None = None


class ApproveAccepted(ContractModel):
    """``202`` of ``POST /proposals/{id}/approve``."""

    execution_id: UUID
    status: Literal["queued"]


class RejectRequest(ContractModel):
    """Body of ``POST /proposals/{id}/reject`` (header ``Idempotency-Key`` required)."""

    reason: Reason


class RejectResponse(ContractModel):
    """``200`` of ``POST /proposals/{id}/reject``."""

    proposal_id: UUID
    status: Literal["rejected"]
