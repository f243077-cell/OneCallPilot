"""``POST /proposals/{id}/challenge|approve|reject`` (C8; API-008…API-010).

Bodies are read raw and validated inside ``Approvals`` so the checks run in
the protocol's order (a missing ``Idempotency-Key`` is reported before a bad
body, and a replayed key returns the stored response).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from oncallpilot_contracts.approval import IDEMPOTENCY_KEY_HEADER, ChallengeResponse

from oncallpilot.api.deps import CurrentUser, get_services
from oncallpilot.api.errors import ChecksUnavailableError
from oncallpilot.approvals.service import Approvals

router = APIRouter(tags=["approvals"])


def _approvals(request: Request) -> Approvals:
    approvals = get_services(request).approvals
    if approvals is None:
        raise ChecksUnavailableError()
    return approvals


@router.post("/proposals/{proposal_id}/challenge", response_model=ChallengeResponse)
async def challenge(request: Request, user: CurrentUser, proposal_id: UUID) -> ChallengeResponse:
    return await _approvals(request).challenge(user.user_id, proposal_id, await request.body())


@router.post("/proposals/{proposal_id}/approve", status_code=202)
async def approve(request: Request, user: CurrentUser, proposal_id: UUID) -> JSONResponse:
    response = await _approvals(request).approve(
        user.user_id, proposal_id, request.headers.get(IDEMPOTENCY_KEY_HEADER),
        await request.body(),
    )  # fmt: skip
    return JSONResponse(response.body, status_code=response.status)


@router.post("/proposals/{proposal_id}/reject")
async def reject(request: Request, user: CurrentUser, proposal_id: UUID) -> JSONResponse:
    response = await _approvals(request).reject(
        user.user_id, proposal_id, request.headers.get(IDEMPOTENCY_KEY_HEADER),
        await request.body(),
    )  # fmt: skip
    return JSONResponse(response.body, status_code=response.status)
