"""``GET /audit``: audit entries, newest first, optionally for one incident (API-012)."""

from __future__ import annotations

import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from oncallpilot_contracts.api import AuditPage
from oncallpilot_contracts.domain import AuditEntry

from oncallpilot.api.deps import CurrentUser, get_services
from oncallpilot.api.errors import ValidationFailedError
from oncallpilot.db import audit as db

router = APIRouter(tags=["audit"])


def _encode(entry_id: int) -> str:
    return base64.urlsafe_b64encode(f"a{entry_id}".encode()).decode().rstrip("=")


def _decode(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        if not raw.startswith("a"):
            raise ValueError("not an audit cursor")
        return int(raw[1:])
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValidationFailedError(
            "invalid cursor", {"errors": [{"loc": ["query", "cursor"], "msg": "invalid cursor"}]}
        ) from exc


@router.get("/audit", response_model=AuditPage)
async def list_audit(
    request: Request,
    _user: CurrentUser,
    incident_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> AuditPage:
    before = _decode(cursor) if cursor else None
    async with get_services(request).pool.acquire() as conn:
        rows = await db.page(conn, incident_id, before, limit + 1)
    items = [AuditEntry(**dict(r)) for r in rows[:limit]]
    next_cursor = _encode(items[-1].id) if len(rows) > limit else None
    return AuditPage(items=items, next_cursor=next_cursor)
