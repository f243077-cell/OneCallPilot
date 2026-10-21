"""``GET /incidents``, ``GET /incidents/{id}``, ``GET /incidents/{id}/log-tail`` and
``POST /incidents/{id}/resolve`` (API-004…API-007, FR-012)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Query, Request
from oncallpilot_contracts.api import IncidentPage, LogTail, ResolveRequest
from oncallpilot_contracts.domain import IncidentDetail, IncidentSummary
from oncallpilot_contracts.enums import IncidentStatus, ServiceName

from oncallpilot.agent.redaction import redact
from oncallpilot.agent.tools.clients import QueryError
from oncallpilot.agent.tools.logql import LogService, selector
from oncallpilot.agent.tools.query_logs import parse_line
from oncallpilot.api.deps import CurrentUser, get_services
from oncallpilot.api.errors import (
    IncidentBusyError,
    LogsUnavailableError,
    NotFoundError,
    RateLimitedError,
    ValidationFailedError,
)
from oncallpilot.db.incidents import InvalidCursorError, PageCursor
from oncallpilot.resolution import resolve_incident

MAX_TAIL_LINES = 50
TAIL_DEFAULT = timedelta(seconds=60)
TAIL_MAX_LOOKBACK = timedelta(minutes=10)

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


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentSummary)
async def resolve(
    request: Request, user: CurrentUser, incident_id: UUID, body: ResolveRequest
) -> IncidentSummary:
    """Manual resolve with a reason; safe to repeat (FR-012)."""
    services = get_services(request)
    try:
        outcome = await resolve_incident(
            services.pool, services.events, incident_id, "manual", f"user:{user.user_id}",
            services.clock(), reason=body.reason,
        )  # fmt: skip
    except LookupError as exc:
        raise NotFoundError("incident not found") from exc
    if outcome in ("busy", "not_allowed"):
        raise IncidentBusyError()
    summary = await services.incidents.get_summary(incident_id)
    if summary is None:
        raise NotFoundError("incident not found")
    return summary


@router.get("/incidents/{incident_id}/log-tail", response_model=LogTail)
async def log_tail(
    request: Request,
    user: CurrentUser,
    incident_id: UUID,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_TAIL_LINES)] = MAX_TAIL_LINES,
) -> LogTail:
    """Recent redacted lines of the incident's service (API-006, SEC-008)."""
    services = get_services(request)
    now = services.clock()
    wait = services.log_tail_limiter.retry_after(user.user_id, now)
    if wait is not None:
        raise RateLimitedError(wait)
    service = await services.incidents.service_of(incident_id)
    if service is None:
        raise NotFoundError("incident not found")
    if services.loki is None:
        raise LogsUnavailableError()
    if since is not None and since.tzinfo is None:
        raise ValidationFailedError(
            "since needs a time zone", {"errors": [{"loc": ["query", "since"], "msg": "naive"}]}
        )
    start = max(since or now - TAIL_DEFAULT, now - TAIL_MAX_LOOKBACK)
    try:
        logql = selector(cast(LogService, service), "ANY", None)
        raw = await services.loki.query_range(logql, start, now, limit)
    except QueryError as exc:
        raise LogsUnavailableError() from exc
    lines = []
    for labels, ns, text in sorted(raw, key=lambda item: item[1]):
        line = parse_line(labels, ns, redact(text))
        lines.append(line.model_copy(update={"msg": redact(line.msg)}))
    return LogTail(lines=lines, until=now)
