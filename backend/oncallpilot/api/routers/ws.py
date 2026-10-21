"""``/ws/incidents``: the live event stream (C3 ``contracts/ws-protocol.md``; API-014…API-017).

One loop per connection handles, in order of arrival: client messages
(``auth``, ``resume``, ``pong``), new ``ocp:events`` entries, the 15-second
``ping``, and the token's expiry. Keeping them in one loop means a ``resume``
can never interleave with live events: replayed events are sent in stream
order, and nothing is sent twice unless a live event arrived between
``hello`` and ``resume`` (the client then drops it by ``state_version``).

- Nothing is sent before the first valid ``auth`` (within 5 s, else close
  4408; an invalid token closes 4401). A later ``auth`` refreshes the token;
  it must be for the same user. When the token expires without a refresh the
  socket is closed with 4401.
- The token travels only inside the ``auth`` message, never in the URL
  (SEC-011); query strings are ignored.
- A client that misses 2 consecutive pongs is closed (within 45 s).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any

from fastapi import APIRouter, WebSocket
from oncallpilot_contracts.ws import (
    AUTH_TIMEOUT_SECONDS,
    CLOSE_AUTH_TIMEOUT,
    CLOSE_UNAUTHORIZED,
    MAX_MISSED_PONGS,
    PING_INTERVAL_SECONDS,
    WS_PATH,
    WsAuth,
    WsClientMessage,
    WsHello,
    WsPing,
    WsPong,
    WsResume,
    WsResyncRequired,
)
from pydantic import ValidationError
from redis.exceptions import RedisError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from oncallpilot.api.auth import AuthUser, TokenVerifier
from oncallpilot.api.deps import get_services
from oncallpilot.api.errors import UnauthorizedError
from oncallpilot.events import EventStream

router = APIRouter()
log = logging.getLogger(__name__)

CONTRACTS_VERSION = version("oncallpilot-contracts")
CLOSE_MISSED_PONGS = 1008  # policy violation; the client reconnects (C3 §7 "any other")
CLOSE_SERVER_ERROR = 1011


@dataclass(frozen=True)
class WsTimings:
    """Protocol timings (C3); tests shorten them."""

    auth_timeout: float = AUTH_TIMEOUT_SECONDS
    ping_interval: float = PING_INTERVAL_SECONDS
    max_missed_pongs: int = MAX_MISSED_PONGS
    read_block_ms: int = 1000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class _Closed(Exception):
    """The connection is over (closed by us or by the client)."""


class WsSession:
    def __init__(
        self,
        ws: WebSocket,
        verifier: TokenVerifier,
        events: EventStream,
        timings: WsTimings,
        now: Callable[[], datetime] = _utcnow,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ws = ws
        self.verifier = verifier
        self.events = events
        self.timings = timings
        self.now = now
        self.monotonic = monotonic

    async def run(self) -> None:
        await self.ws.accept()
        try:
            user = await self._authenticate()
            await self._serve(user)
        except _Closed:
            pass

    # --- phases -------------------------------------------------------------

    async def _authenticate(self) -> AuthUser:
        try:
            text = await asyncio.wait_for(self._receive(), timeout=self.timings.auth_timeout)
        except TimeoutError:
            await self._close(CLOSE_AUTH_TIMEOUT, "auth timeout")
            raise _Closed() from None
        message = self._parse(text)
        if not isinstance(message, WsAuth):
            await self._close(CLOSE_UNAUTHORIZED, "auth required")
            raise _Closed()
        return await self._verify(message.token, None)

    async def _serve(self, user: AuthUser) -> None:
        latest = await self.events.latest_id()
        await self._send(
            WsHello(
                type="hello",
                server_time=self.now(),
                contracts_version=CONTRACTS_VERSION,
                latest_event_id=latest,
            ).model_dump(mode="json")
        )
        cursor = latest or "0-0"
        receiver: asyncio.Task[str] = asyncio.create_task(self._receive())
        reader = self._start_reader(cursor)
        next_ping = self.monotonic() + self.timings.ping_interval
        missed = 0
        try:
            while True:
                wait = min(
                    next_ping - self.monotonic(),
                    (user.expires_at - self.now()).total_seconds(),
                )
                done, _ = await asyncio.wait(
                    {receiver, reader},
                    timeout=max(wait, 0.0),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self.now() >= user.expires_at:
                    await self._close(CLOSE_UNAUTHORIZED, "token expired")
                    return
                if self.monotonic() >= next_ping:
                    if missed >= self.timings.max_missed_pongs:
                        await self._close(CLOSE_MISSED_PONGS, "missed pongs")
                        return
                    await self._send(WsPing(type="ping").model_dump(mode="json"))
                    missed += 1
                    next_ping += self.timings.ping_interval

                if receiver in done:
                    message = self._parse(receiver.result())
                    receiver = asyncio.create_task(self._receive())
                    if isinstance(message, WsPong):
                        missed = 0
                    elif isinstance(message, WsAuth):
                        user = await self._verify(message.token, user)
                    elif isinstance(message, WsResume):
                        if await self.events.resumable_from(message.last_event_id):
                            reader.cancel()
                            with contextlib.suppress(asyncio.CancelledError, RedisError):
                                await reader
                            cursor = message.last_event_id
                            reader = self._start_reader(cursor)
                        else:
                            await self._send(
                                WsResyncRequired(type="resync_required").model_dump(mode="json")
                            )

                if reader in done:
                    try:
                        envelopes = reader.result()
                    except RedisError as exc:
                        log.error("ocp:events read failed: %s", type(exc).__name__)
                        await self._close(CLOSE_SERVER_ERROR, "event stream unavailable")
                        return
                    for envelope in envelopes:
                        await self._send(envelope)
                        cursor = envelope["event_id"]
                    reader = self._start_reader(cursor)
        finally:
            for task in (receiver, reader):
                task.cancel()
            for task in (receiver, reader):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    # --- helpers ------------------------------------------------------------

    def _start_reader(self, cursor: str) -> asyncio.Task[list[dict[str, Any]]]:
        return asyncio.create_task(
            self.events.read_after(cursor, block_ms=self.timings.read_block_ms)
        )

    async def _verify(self, token: str, current: AuthUser | None) -> AuthUser:
        try:
            user = await self.verifier.verify(token)
        except UnauthorizedError:
            await self._close(CLOSE_UNAUTHORIZED, "invalid token")
            raise _Closed() from None
        if current is not None and user.user_id != current.user_id:
            await self._close(CLOSE_UNAUTHORIZED, "token for another user")
            raise _Closed()
        return user

    def _parse(self, text: str) -> WsAuth | WsResume | WsPong | None:
        """The client message, or None for anything that is not one (ignored, MOB-020)."""
        try:
            return WsClientMessage.model_validate_json(text).root
        except ValidationError:
            return None

    async def _receive(self) -> str:
        message = await self.ws.receive()
        if message["type"] == "websocket.disconnect":
            raise _Closed()
        text = message.get("text")
        return text if isinstance(text, str) else ""

    async def _send(self, body: dict[str, Any]) -> None:
        try:
            await self.ws.send_json(body)
        except (WebSocketDisconnect, RuntimeError) as exc:
            raise _Closed() from exc

    async def _close(self, code: int, reason: str) -> None:
        if self.ws.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(RuntimeError, WebSocketDisconnect):
                await self.ws.close(code=code, reason=reason)


@router.websocket(WS_PATH)
async def incidents_ws(websocket: WebSocket) -> None:
    services = get_services(websocket)
    session = WsSession(websocket, services.verifier, services.events, services.ws_timings)
    await session.run()
