"""``POST /devices`` and ``DELETE /devices/{id}`` (API-011, FR-018)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response
from oncallpilot_contracts.api import DeviceRegister, DeviceRegistered

from oncallpilot.api.deps import CurrentUser, get_services
from oncallpilot.api.errors import NotFoundError
from oncallpilot.db import agent as audit_db
from oncallpilot.db import devices as db

router = APIRouter(tags=["devices"])


@router.post("/devices", response_model=DeviceRegistered)
async def register_device(
    request: Request, user: CurrentUser, body: DeviceRegister
) -> DeviceRegistered:
    services = get_services(request)
    async with services.pool.acquire() as conn, conn.transaction():
        device_id = await db.register(
            conn, user.user_id, body.fcm_token, body.app_version, services.clock()
        )
        # The token itself is never written to the audit log.
        await audit_db.audit(
            conn, f"user:{user.user_id}", "device.registered", None, "device", device_id,
            {"platform": body.platform, "app_version": body.app_version},
        )  # fmt: skip
    return DeviceRegistered(device_id=device_id)


@router.delete("/devices/{device_id}", status_code=204)
async def delete_device(request: Request, user: CurrentUser, device_id: UUID) -> Response:
    async with get_services(request).pool.acquire() as conn:
        if not await db.unregister(conn, device_id, user.user_id):
            raise NotFoundError("device not found")
    return Response(status_code=204)
