"""C8 approval bodies, and the protocol document covering every approve error code."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from samples import sha, uid

from oncallpilot_contracts import approval

PROTOCOL_DOC = Path(__file__).resolve().parents[2] / "approval-protocol.md"
NONCE = "9f0c3e5a7b1d2f4e6a8c0b3d5f7e9a1c2b4d6f8e0a3c5e7b9d1f2a4c6e8b0d3f"


def approve_body() -> dict[str, Any]:
    return {
        "challenge_id": uid(52),
        "nonce": NONCE,
        "proposal_fingerprint": sha("proposal fingerprint"),
        "auth_method": "biometric",
        "device_id": uid(53),
    }


def rejects(model: type[BaseModel], data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_approve_body_and_optional_device() -> None:
    approval.ApproveRequest.model_validate(approve_body())
    body = approve_body()
    del body["device_id"]
    assert approval.ApproveRequest.model_validate(body).device_id is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"nonce": NONCE.upper()},
        {"nonce": NONCE[:-2]},
        {"auth_method": "pin"},
        {"proposal_fingerprint": "stale"},
        {"approved": True},
    ],
)
def test_invalid_approve_body(overrides: dict[str, Any]) -> None:
    rejects(approval.ApproveRequest, {**approve_body(), **overrides})


def test_challenge_response() -> None:
    response = {
        "challenge_id": uid(52),
        "nonce": NONCE,
        "expires_at": "2026-10-14T09:03:55Z",
        "approval_requirement": "biometric",
    }
    approval.ChallengeResponse.model_validate(response)
    rejects(approval.ChallengeResponse, {**response, "approval_requirement": "pin"})


@pytest.mark.parametrize(("reason", "ok"), [("no", False), ("ok!", True), ("x" * 500, True)])
def test_reject_reason_length(reason: str, ok: bool) -> None:
    if ok:
        approval.RejectRequest.model_validate({"reason": reason})
    else:
        rejects(approval.RejectRequest, {"reason": reason})
    rejects(approval.RejectRequest, {"reason": "x" * 501})


def test_fixed_statuses() -> None:
    rejects(approval.ApproveAccepted, {"execution_id": uid(40), "status": "running"})
    rejects(approval.RejectResponse, {"proposal_id": uid(30), "status": "approved"})


def test_protocol_doc_names_every_approve_error() -> None:
    text = PROTOCOL_DOC.read_text("utf-8")
    for code in (
        "IDEMPOTENCY_KEY_REQUIRED",
        "IDEMPOTENCY_CONFLICT",
        "CHALLENGE_INVALID",
        "STALE_PROPOSAL",
        "PROPOSAL_NOT_PENDING",
        "PROPOSAL_EXPIRED",
        "BIOMETRIC_REQUIRED",
        "RATE_LIMITED",
        "COOLDOWN_ACTIVE",
    ):
        assert code in text, code
    assert approval.IDEMPOTENCY_KEY_HEADER in text
