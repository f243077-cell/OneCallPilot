"""``GET /incidents`` and ``GET /incidents/{id}`` (API-004, API-005)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from oncallpilot_contracts.api import IncidentPage
from oncallpilot_contracts.domain import IncidentDetail
from oncallpilot_contracts.enums import IncidentStatus, ServiceName

from oncallpilot.api.deps import CurrentUser, get_services
from oncallpilot.api.errors import NotFoundError, ValidationFailedError
from oncallpilot.db.incidents import InvalidCursorError, PageCursor

router = APIRouter(tags=["incidents"])


@router.get("/incidents", response_model=IncidentPage)
async def list_incidents(
    request: Request,
    _user: CurrentUser,
    status: IncidentStatus | None = None,
    service: ServiceName | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> IncidentPage:
    try:
        after = PageCursor.decode(cursor) if cursor else None
    except InvalidCursorError as exc:
        raise ValidationFailedError(
            "invalid cursor", {"errors": [{"loc": ["query", "cursor"], "msg": "invalid cursor"}]}
        ) from exc
    items, next_cursor = await get_services(request).incidents.list_page(
        status=status, service=service, limit=limit, cursor=after
    )
    return IncidentPage(items=items, next_cursor=next_cursor.encode() if next_cursor else None)


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident(request: Request, _user: CurrentUser, incident_id: UUID) -> IncidentDetail:
    detail = await get_services(request).incidents.get_detail(incident_id)
    if detail is None:
        raise NotFoundError("incident not found")
    return detail
