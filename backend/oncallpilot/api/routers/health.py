"""``GET /healthz`` (API-002): unauthenticated; 503 when the database or Redis is down."""

from __future__ import annotations

import asyncio
import logging
from importlib.metadata import version

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from oncallpilot_contracts.api import Healthz
from redis.exceptions import RedisError

from oncallpilot.api.deps import get_services
from oncallpilot.db.pool import ping as db_ping

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)

CONTRACTS_VERSION = version("oncallpilot-contracts")
CHECK_TIMEOUT_SECONDS = 2.0


@router.get("/healthz", response_model=Healthz)
async def healthz(request: Request) -> JSONResponse:
    services = get_services(request)
    db_up = await db_ping(services.pool, CHECK_TIMEOUT_SECONDS)
    try:
        redis_up = bool(
            await asyncio.wait_for(services.redis.ping(), timeout=CHECK_TIMEOUT_SECONDS)
        )
    except (RedisError, OSError, TimeoutError):
        redis_up = False
    body = Healthz(
        status="ok" if db_up and redis_up else "degraded",
        contracts_version=CONTRACTS_VERSION,
        db="up" if db_up else "down",
        redis="up" if redis_up else "down",
    )
    if body.status != "ok":
        log.warning("healthz degraded: db=%s redis=%s", body.db, body.redis)
    return JSONResponse(
        body.model_dump(mode="json"), status_code=200 if db_up and redis_up else 503
    )
