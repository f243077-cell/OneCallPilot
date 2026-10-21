"""Which events push, and to whom (C9 §2; FR-018).

``Notifier.on_event`` is called by ``EventStream`` after each published
event and maps it to a push type:

- ``incident.opened`` → ``incident.opened``
- ``proposal.created`` (rollbacks too) → ``proposal.created``
- ``incident.updated`` to ``escalated`` → ``incident.escalated``
- ``incident.updated`` to ``action_failed`` → ``incident.action_failed``
- ``incident.resolved`` → ``incident.resolved``

Each push goes to every device with ``push_enabled`` when the incident's
severity is at or above ``monitor_settings.notify.min_push_severity``. A
token FCM reports as unregistered is disabled (``ocp_backend`` has no DELETE,
DB-011). Sending runs in the background and never fails the change that
caused it.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import httpx
from oncallpilot_contracts.push import PushPayload, PushType

from oncallpilot.db import devices as db
from oncallpilot.push.fcm import FcmTransport, PushTransport, ServiceAccount

log = logging.getLogger(__name__)

RANK = {"sev1": 1, "sev2": 2, "sev3": 3}


def push_type(event: str, data: dict[str, Any]) -> PushType | None:
    if event in ("incident.opened", "proposal.created", "incident.resolved"):
        return event  # type: ignore[return-value]
    if event == "incident.updated" and data.get("status") == "escalated":
        return "incident.escalated"
    if event == "incident.updated" and data.get("status") == "action_failed":
        return "incident.action_failed"
    return None


class Notifier:
    def __init__(self, pool: asyncpg.Pool, transport: PushTransport) -> None:
        self._pool = pool
        self._transport = transport
        self._tasks: set[asyncio.Task[None]] = set()

    def on_event(self, event: str, incident_id: UUID, data: dict[str, Any]) -> None:
        kind = push_type(event, data)
        if kind is None:
            return
        task = asyncio.create_task(self._send_logged(kind, incident_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def _send_logged(self, kind: PushType, incident_id: UUID) -> None:
        try:
            await self.send(kind, incident_id)
        except (asyncpg.PostgresError, OSError) as exc:
            log.error("push %s failed: %s", kind, type(exc).__name__,
                      extra={"incident_id": incident_id})  # fmt: skip

    async def send(self, kind: PushType, incident_id: UUID) -> int:
        """Sends one message per enabled device; returns how many were sent."""
        async with self._pool.acquire() as conn:
            incident = await db.incident_for_push(conn, incident_id)
            if incident is None:
                return 0
            minimum = await db.min_push_severity(conn)
            if RANK[incident["severity"]] > RANK.get(minimum, 3):
                return 0
            devices = await db.enabled_devices(conn)
        payload = PushPayload(
            type=kind,
            incident_id=incident_id,
            severity=incident["severity"],
            service=incident["service"],
            title=str(incident["title"])[:120],
        )
        sent = 0
        for device in devices:
            status = await self._transport.send(device["fcm_token"], payload)
            if status == "sent":
                sent += 1
            elif status == "unregistered":
                async with self._pool.acquire() as conn:
                    await db.disable(conn, device["id"])
                log.info("device token unregistered; push disabled",
                         extra={"incident_id": incident_id})  # fmt: skip
        return sent


def build_notifier(
    pool: asyncpg.Pool, http: httpx.AsyncClient, credentials: Path | None
) -> Notifier | None:
    """FCM pushes when a service account is mounted; none otherwise (logged)."""
    if credentials is None:
        log.warning("FCM_CREDENTIALS_PATH is not set: no push notifications")
        return None
    return Notifier(pool, FcmTransport(http, ServiceAccount.load(credentials)))
