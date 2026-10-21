"""The signed ``execute`` request (C5; architecture §5.9 step 6; SEC-001, SEC-002).

This is the only module that builds an ``execute`` message. It is signed with
``ACTION_SIGNING_KEY``, which only backend-api and the runner hold.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from oncallpilot_contracts.runner import REQUESTS_STREAM, STREAM_FIELD, RunnerRequest
from oncallpilot_contracts.signing import sign
from redis.asyncio import Redis

EXECUTE_TTL_SECONDS = 60


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def execute_message(
    *,
    execution_id: uuid.UUID,
    proposal_id: uuid.UUID,
    action: str,
    params: dict[str, Any],
    state_fingerprint: str,
    idempotency_key: uuid.UUID,
    approved_by: uuid.UUID,
    approved_at: datetime,
    catalogue_version: str,
    key: bytes,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "type": "execute",
        "request_id": str(uuid.uuid4()),
        "execution_id": str(execution_id),
        "proposal_id": str(proposal_id),
        "action": action,
        "params": params,
        "state_fingerprint": state_fingerprint,
        "idempotency_key": str(idempotency_key),
        "approved_by": str(approved_by),
        "approved_at": _iso(approved_at),
        "issued_at": _iso(approved_at),
        "expires_at": _iso(approved_at + timedelta(seconds=EXECUTE_TTL_SECONDS)),
        "catalogue_version": catalogue_version,
    }
    message["sig"] = sign(message, key)
    RunnerRequest.model_validate(message)  # the runner would refuse anything else
    return message


async def send_execute(redis: Redis, message: dict[str, Any]) -> None:
    await redis.xadd(REQUESTS_STREAM, {STREAM_FIELD: json.dumps(message, separators=(",", ":"))})
