"""The ``ocp:events`` stream (architecture §5.12, C3 ``contracts/ws-protocol.md``).

Every persisted change to an incident publishes exactly one event (FR-019).
An entry has one field, ``msg``: the C3 event envelope as JSON, without
``type`` and ``event_id``; the gateway adds both when it forwards the entry,
and the Redis stream ID *is* the ``event_id`` (monotonic, used for resume).

``publish`` validates the full envelope against the contract before writing,
so a malformed event never reaches the stream. ``evidence.added`` payloads
must already be cut with ``cut_evidence_payload`` (§4.1).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from oncallpilot_contracts.ws import WsEvent
from pydantic import BaseModel, TypeAdapter
from redis.asyncio import Redis
from redis.exceptions import ResponseError

EVENTS_STREAM = "ocp:events"
EVENTS_MAXLEN = 10_000

_EVENT = TypeAdapter[Any](WsEvent)


def parse_id(event_id: str) -> tuple[int, int]:
    """``"<ms>-<seq>"`` as a comparable tuple; raises ``ValueError`` otherwise."""
    ms, sep, seq = event_id.partition("-")
    if not sep or not ms.isdigit() or not seq.isdigit():
        raise ValueError(f"not a stream id: {event_id!r}")
    return int(ms), int(seq)


def envelope(event_id: str, fields: dict[str, str]) -> dict[str, Any]:
    """The WebSocket ``event`` message for one stream entry."""
    body: dict[str, Any] = json.loads(fields["msg"])
    return {"type": "event", "event_id": event_id, **body}


class EventStream:
    def __init__(self, redis: Redis, maxlen: int = EVENTS_MAXLEN) -> None:
        # ``decode_responses=True``: IDs and fields come back as str.
        self._redis = redis
        self._maxlen = maxlen

    async def publish(
        self,
        event: str,
        incident_id: UUID,
        state_version: int,
        ts: datetime,
        data: BaseModel | dict[str, Any],
    ) -> str:
        """Validate the event and append it; returns its ``event_id``."""
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        body = {
            "event": event,
            "incident_id": str(incident_id),
            "state_version": state_version,
            "ts": ts,
            "data": payload,
        }
        checked = _EVENT.validate_python({"type": "event", "event_id": "0-1", **body})
        stored = checked.model_dump(mode="json", exclude={"type", "event_id"})
        event_id = await self._redis.xadd(
            EVENTS_STREAM,
            {"msg": json.dumps(stored, separators=(",", ":"))},
            maxlen=self._maxlen,
            approximate=True,
        )
        return str(event_id)

    async def latest_id(self) -> str | None:
        entries = await self._redis.xrevrange(EVENTS_STREAM, count=1)
        return str(entries[0][0]) if entries else None

    async def read_after(
        self, after: str, block_ms: int | None, count: int = 100
    ) -> list[dict[str, Any]]:
        """Envelopes of the entries after ``after``, oldest first (waits up to ``block_ms``)."""
        response: Any = await self._redis.xread({EVENTS_STREAM: after}, count=count, block=block_ms)
        if not response:
            return []
        [(_, entries)] = response
        return [envelope(str(entry_id), fields) for entry_id, fields in entries]

    async def resumable_from(self, last_event_id: str) -> bool:
        """True when every entry after ``last_event_id`` is still in the stream.

        False when the ID is older than the oldest retained entry (trimmed by
        MAXLEN, or from before a Redis restart), newer than anything the stream
        produced, or malformed: the client must resync (API-016).
        """
        try:
            last = parse_id(last_event_id)
            info = await self._redis.xinfo_stream(EVENTS_STREAM)
        except ValueError:
            return False
        except ResponseError as exc:  # XINFO on a missing stream
            if "no such key" in str(exc).lower():
                return False
            raise
        first_entry = info.get("first-entry")
        if not first_entry:
            return False
        first = parse_id(str(first_entry[0]))
        newest = parse_id(str(info["last-generated-id"]))
        return first <= last <= newest
