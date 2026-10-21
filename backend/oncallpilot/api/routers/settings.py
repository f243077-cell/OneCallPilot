"""``GET/PUT /settings`` (FR-023, API-013, ADR-20).

Editable only while ``BENCHMARK_LOCK`` is off (``423``); ``version`` is an
optimistic lock (``409``). Thresholds must name real detector settings, so a
typo is refused instead of silently doing nothing. Every change bumps
``version`` and therefore the ``detector_config_version`` of new incidents.
"""

from __future__ import annotations

from typing import Any, get_args

from fastapi import APIRouter, Request
from oncallpilot_contracts.api import MonitorSettingsUpdate
from oncallpilot_contracts.domain import MonitorSettings
from oncallpilot_contracts.enums import ServiceName
from pydantic import ValidationError

from oncallpilot.api.deps import CurrentUser, get_services
from oncallpilot.api.errors import (
    BenchmarkLockedError,
    ValidationFailedError,
    VersionConflictError,
)
from oncallpilot.db import agent as audit_db
from oncallpilot.db import settings as db
from oncallpilot.detector.config import load_config

router = APIRouter(tags=["settings"])

LOCK_REASON = "Locked while the benchmark runs (BENCHMARK_LOCK)"
DEFAULTS: dict[str, Any] = {
    "services": list(get_args(ServiceName)),
    "thresholds": {},
    "notify": {"min_push_severity": "sev3"},
}


def _view(request: Request, row: Any) -> MonitorSettings:
    services = get_services(request)
    locked = services.ingest.benchmark_lock
    stored = row["settings"] if row is not None else DEFAULTS
    return MonitorSettings(
        version=row["version"] if row is not None else 1,
        services=stored.get("services", DEFAULTS["services"]),
        thresholds=stored.get("thresholds", {}),
        notify=stored.get("notify", DEFAULTS["notify"]),
        locked=locked,
        lock_reason=LOCK_REASON if locked else None,
        updated_by=row["updated_by"] if row is not None else None,
        updated_at=row["updated_at"] if row is not None else services.clock(),
    )


@router.get("/settings", response_model=MonitorSettings)
async def get_settings(request: Request, _user: CurrentUser) -> MonitorSettings:
    async with get_services(request).pool.acquire() as conn:
        row = await db.current(conn)
    return _view(request, row)


@router.put("/settings", response_model=MonitorSettings)
async def put_settings(
    request: Request, user: CurrentUser, body: MonitorSettingsUpdate
) -> MonitorSettings:
    services = get_services(request)
    if services.ingest.benchmark_lock:
        raise BenchmarkLockedError()
    try:
        load_config(services.ingest.detector_config, dict(body.thresholds))
    except (ValueError, ValidationError) as exc:
        raise ValidationFailedError(
            "unknown or invalid threshold",
            {"errors": [{"loc": ["body", "thresholds"], "msg": "not a valid detector setting"}]},
        ) from exc
    now = services.clock()
    async with services.pool.acquire() as conn, conn.transaction():
        row = await db.current(conn, lock=True)
        current = row["version"] if row is not None else 1
        if body.version != current:
            raise VersionConflictError()
        stored = {
            "services": list(body.services),
            "thresholds": dict(body.thresholds),
            "notify": body.notify.model_dump(mode="json"),
        }
        await db.save(conn, current + 1, stored, user.user_id, now)
        await audit_db.audit(
            conn, f"user:{user.user_id}", "settings.updated", None, "settings", None,
            {"from_version": current, "to_version": current + 1, "settings": stored},
        )  # fmt: skip
        row = await db.current(conn)
    return _view(request, row)
