"""backend-api application factory (architecture §2.2, ADR-09).

``create_app`` takes a factory for the services the routes use, so tests can
hand in a test database, a fake Redis, and a local JWKS. ``create_production_app``
wires the real ones from the environment; run it with
``uvicorn --factory oncallpilot.api.app:create_production_app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import asyncpg
import httpx
from fastapi import FastAPI
from oncallpilot_contracts.signing import key_from_hex
from pydantic import SecretStr
from redis.asyncio import Redis

from oncallpilot.api.auth import JwksCache, TokenVerifier, http_jwks_fetcher
from oncallpilot.api.errors import install_error_handlers
from oncallpilot.api.routers import health, incidents, ingest, proposals, ws
from oncallpilot.api.routers.ws import WsTimings
from oncallpilot.approvals.service import Approvals
from oncallpilot.db.incidents import IncidentRepository
from oncallpilot.db.pool import create_pool
from oncallpilot.detector.config import read_config
from oncallpilot.events import EventStream
from oncallpilot.jsonlog import configure_logging
from oncallpilot.queues import Queues
from oncallpilot.settings import ApiSettings

REDIS_TIMEOUT_SECONDS = 2.0


class Pinger(Protocol):
    def ping(self) -> Awaitable[bool]: ...


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class IngestConfig:
    """What ``POST /ingest/alert`` needs besides the database (API-003, ADR-20)."""

    token: SecretStr
    agent_enabled: bool
    detector_config: bytes


@dataclass(frozen=True)
class Services:
    pool: asyncpg.Pool
    redis: Pinger
    verifier: TokenVerifier
    incidents: IncidentRepository
    events: EventStream
    queues: Queues
    ingest: IngestConfig
    ws_timings: WsTimings = WsTimings()
    clock: Callable[[], datetime] = field(default=utcnow)
    approvals: Approvals | None = None  # needs a real Redis; None in tests that do not approve


ServicesFactory = Callable[[], AbstractAsyncContextManager[Services]]


def create_app(services: ServicesFactory) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with services() as built:
            app.state.services = built
            yield

    app = FastAPI(title="OnCallPilot backend-api", lifespan=lifespan, docs_url=None, redoc_url=None)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(incidents.router)
    app.include_router(ingest.router)
    app.include_router(proposals.router)
    app.include_router(ws.router)
    return app


def production_services(settings: ApiSettings) -> ServicesFactory:
    @asynccontextmanager
    async def build() -> AsyncIterator[Services]:
        pool = await create_pool(settings.database_url.get_secret_value())
        redis: Redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            # Above the WebSocket gateway's XREAD block (1 s).
            socket_timeout=REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        )
        async with httpx.AsyncClient() as http:
            verifier = TokenVerifier(
                JwksCache(http_jwks_fetcher(http, settings.jwks_url)), settings.jwt_issuer
            )
            events = EventStream(redis)
            approvals = Approvals(
                pool=pool,
                redis=redis,
                events=events,
                action_signing_key=key_from_hex(settings.action_signing_key.get_secret_value()),
                clock=utcnow,
            )
            try:
                yield Services(
                    pool=pool,
                    redis=redis,
                    verifier=verifier,
                    incidents=IncidentRepository(pool),
                    events=events,
                    queues=Queues(redis),
                    ingest=IngestConfig(
                        token=settings.ingest_token,
                        agent_enabled=settings.agent_enabled,
                        detector_config=read_config(settings.detector_config_path),
                    ),
                    approvals=approvals,
                )
            finally:
                await redis.aclose()
                await pool.close()

    return build


def create_production_app() -> FastAPI:
    configure_logging()
    return create_app(production_services(ApiSettings()))
