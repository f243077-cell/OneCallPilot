"""backend-api application factory (architecture §2.2, ADR-09).

``create_app`` takes a factory for the services the routes use, so tests can
hand in a test database, a fake Redis, and a local JWKS. ``create_production_app``
wires the real ones from the environment; run it with
``uvicorn --factory oncallpilot.api.app:create_production_app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

import asyncpg
import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from oncallpilot.api.auth import JwksCache, TokenVerifier, http_jwks_fetcher
from oncallpilot.api.errors import install_error_handlers
from oncallpilot.api.routers import health, incidents
from oncallpilot.db.incidents import IncidentRepository
from oncallpilot.db.pool import create_pool
from oncallpilot.jsonlog import configure_logging
from oncallpilot.settings import ApiSettings

REDIS_TIMEOUT_SECONDS = 2.0


class Pinger(Protocol):
    def ping(self) -> Awaitable[bool]: ...


@dataclass(frozen=True)
class Services:
    pool: asyncpg.Pool
    redis: Pinger
    verifier: TokenVerifier
    incidents: IncidentRepository


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
    return app


def production_services(settings: ApiSettings) -> ServicesFactory:
    @asynccontextmanager
    async def build() -> AsyncIterator[Services]:
        pool = await create_pool(settings.database_url.get_secret_value())
        redis: Redis = Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_timeout=REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        )
        async with httpx.AsyncClient() as http:
            verifier = TokenVerifier(
                JwksCache(http_jwks_fetcher(http, settings.jwks_url)), settings.jwt_issuer
            )
            try:
                yield Services(
                    pool=pool, redis=redis, verifier=verifier, incidents=IncidentRepository(pool)
                )
            finally:
                await redis.aclose()
                await pool.close()

    return build


def create_production_app() -> FastAPI:
    configure_logging()
    return create_app(production_services(ApiSettings()))
