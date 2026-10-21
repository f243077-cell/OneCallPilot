"""Idempotency-Key handling for approve and reject (C8 §5; TEST-005).

``ocp:idem:{op}:{user_id}:{key}`` holds a hash of the request and, once the
first attempt finishes, its response (success or error) for 24 h. The first
attempt claims the key with ``SET NX`` (state ``pending``), so a concurrent
double tap waits for that response instead of running twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from oncallpilot.api.errors import (
    ChecksUnavailableError,
    IdempotencyConflictError,
    IdempotencyKeyRequiredError,
)

TTL_SECONDS = 24 * 3600
WAIT_SECONDS = 10.0
POLL_SECONDS = 0.05


def parse_key(header: str | None) -> uuid.UUID:
    if not header:
        raise IdempotencyKeyRequiredError()
    try:
        return uuid.UUID(header)
    except ValueError as exc:
        raise IdempotencyKeyRequiredError() from exc


def request_hash(proposal_id: uuid.UUID, raw_body: bytes) -> str:
    """Same proposal and same JSON body (key order and whitespace ignored) → same hash."""
    try:
        canonical = json.dumps(json.loads(raw_body), sort_keys=True, separators=(",", ":"))
    except ValueError:
        canonical = raw_body.decode("utf-8", "replace")
    return hashlib.sha256(f"{proposal_id}\n{canonical}".encode()).hexdigest()


@dataclass(frozen=True)
class StoredResponse:
    status: int
    body: dict[str, Any]


class IdempotencyStore:
    def __init__(self, redis: Redis, wait_seconds: float = WAIT_SECONDS) -> None:
        self._redis = redis
        self._wait = wait_seconds

    @staticmethod
    def _key(op: str, user_id: uuid.UUID, key: uuid.UUID) -> str:
        return f"ocp:idem:{op}:{user_id}:{key}"

    async def begin(
        self, op: str, user_id: uuid.UUID, key: uuid.UUID, body_hash: str
    ) -> StoredResponse | None:
        """None: this attempt owns the key and must call ``finish`` or ``release``.
        Otherwise the stored response to replay."""
        name = self._key(op, user_id, key)
        claimed = await self._redis.set(
            name, json.dumps({"hash": body_hash, "state": "pending"}), nx=True, ex=TTL_SECONDS
        )
        if claimed:
            return None
        deadline = time.monotonic() + self._wait
        while True:
            raw: Any = await self._redis.get(name)
            if raw is None:  # released after an unexpected error: claim again
                return await self.begin(op, user_id, key, body_hash)
            stored = json.loads(raw)
            if stored["hash"] != body_hash:
                raise IdempotencyConflictError()
            if stored["state"] == "done":
                return StoredResponse(int(stored["status"]), dict(stored["body"]))
            if time.monotonic() >= deadline:
                raise ChecksUnavailableError("the first attempt with this key is still running")
            await asyncio.sleep(POLL_SECONDS)

    async def finish(
        self,
        op: str,
        user_id: uuid.UUID,
        key: uuid.UUID,
        body_hash: str,
        status: int,
        body: dict[str, Any],
    ) -> None:
        value = {"hash": body_hash, "state": "done", "status": status, "body": body}
        await self._redis.set(self._key(op, user_id, key), json.dumps(value), ex=TTL_SECONDS)

    async def release(self, op: str, user_id: uuid.UUID, key: uuid.UUID) -> None:
        await self._redis.delete(self._key(op, user_id, key))
