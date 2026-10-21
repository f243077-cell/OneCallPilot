"""Supabase JWT verification through the project's JWKS (SEC-010, architecture §7.11).

- Only asymmetric algorithms are accepted; HS256 (the legacy shared secret)
  is refused, because both Supabase projects use an asymmetric signing key.
- The JWKS is cached for 10 minutes and refetched when a token names an
  unknown ``kid`` (at most once every 30 s, so bad tokens cannot hammer
  Supabase).
- Checks: signature, ``exp``, ``aud = authenticated``, ``iss`` and a UUID
  ``sub``. The token comes only from the ``Authorization`` header, never from
  a URL or query string (``CLAUDE.md`` §6.12).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import Request

from oncallpilot.api.errors import UnauthorizedError

log = logging.getLogger(__name__)

AUDIENCE = "authenticated"
ALLOWED_ALGORITHMS = ("ES256", "RS256", "EdDSA")
JWKS_TTL_SECONDS = 600.0
JWKS_MIN_REFETCH_SECONDS = 30.0
JWKS_FETCH_TIMEOUT_SECONDS = 5.0

JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class JwksUnavailableError(Exception):
    """The JWKS could not be fetched or parsed."""


def http_jwks_fetcher(client: httpx.AsyncClient, url: str) -> JwksFetcher:
    async def fetch() -> dict[str, Any]:
        try:
            response = await client.get(url, timeout=JWKS_FETCH_TIMEOUT_SECONDS)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JwksUnavailableError(str(exc)) from exc
        if not isinstance(body, dict):
            raise JwksUnavailableError("JWKS is not a JSON object")
        return body

    return fetch


class JwksCache:
    """Keys by ``kid``, refreshed after ``ttl`` seconds or on an unknown ``kid``."""

    def __init__(
        self,
        fetch: JwksFetcher,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = JWKS_TTL_SECONDS,
        min_refetch: float = JWKS_MIN_REFETCH_SECONDS,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._ttl = ttl
        self._min_refetch = min_refetch
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> jwt.PyJWK | None:
        now = self._clock()
        if self._fetched_at is None or now - self._fetched_at >= self._ttl:
            await self._refresh(now)
        elif kid not in self._keys and now - self._fetched_at >= self._min_refetch:
            await self._refresh(now)
        return self._keys.get(kid)

    async def _refresh(self, now: float) -> None:
        async with self._lock:
            if self._fetched_at is not None and now - self._fetched_at < self._min_refetch:
                return  # another request refreshed it meanwhile
            body = await self._fetch()
            keys: dict[str, jwt.PyJWK] = {}
            for entry in body.get("keys", []):
                if not isinstance(entry, dict) or "kid" not in entry:
                    continue
                if entry.get("alg") not in ALLOWED_ALGORITHMS:
                    continue  # symmetric or unknown keys are never used
                try:
                    keys[str(entry["kid"])] = jwt.PyJWK(entry)
                except jwt.PyJWTError as exc:
                    log.warning("skipping an unusable JWKS key: %s", type(exc).__name__)
            self._keys = keys
            self._fetched_at = now


@dataclass(frozen=True)
class AuthUser:
    user_id: UUID
    expires_at: datetime


class TokenVerifier:
    def __init__(self, jwks: JwksCache, issuer: str, leeway_seconds: int = 30) -> None:
        self._jwks = jwks
        self._issuer = issuer
        self._leeway = leeway_seconds

    async def verify(self, token: str) -> AuthUser:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise UnauthorizedError() from exc
        alg = header.get("alg")
        kid = header.get("kid")
        if alg not in ALLOWED_ALGORITHMS or not isinstance(kid, str):
            raise UnauthorizedError()
        try:
            key = await self._jwks.get(kid)
        except JwksUnavailableError as exc:
            log.error("JWKS unavailable: %s", exc)
            raise UnauthorizedError() from exc  # fail closed
        if key is None or key.algorithm_name != alg:
            raise UnauthorizedError()
        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=[alg],
                audience=AUDIENCE,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "aud", "iss", "sub"]},
            )
            return AuthUser(
                user_id=UUID(str(claims["sub"])),
                expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
            )
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise UnauthorizedError() from exc


def bearer_token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthorizedError()
    return token.strip()
