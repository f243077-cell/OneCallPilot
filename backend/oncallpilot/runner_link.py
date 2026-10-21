"""Dry runs through the runner (C5; architecture §5.6 step 5, §9.5; RUN-007, SEC-003).

backend-worker sends ``dry_run`` requests signed with ``RUNNER_LINK_KEY`` (it
never holds ``ACTION_SIGNING_KEY``, ADR-10) and waits up to 15 s for the
``dry_run_result`` with the same ``request_id``. A result is trusted only
after its signature verifies; an unsigned, forged or malformed entry is
ignored. One retry, then the dry run counts as unavailable.

The results stream is read without a consumer group (``XREAD``), so the
worker's result consumer (B2.10) still sees every entry.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from oncallpilot_contracts.runner import (
    REQUESTS_STREAM,
    RESULTS_STREAM,
    STREAM_FIELD,
    RunnerResult,
)
from oncallpilot_contracts.signing import sign, verify
from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

DRY_RUN_WAIT_SECONDS = 15.0
REQUEST_TTL_SECONDS = 60


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class RunnerLink:
    redis: Redis
    link_key: bytes  # RUNNER_LINK_KEY, decoded
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    wait_seconds: float = DRY_RUN_WAIT_SECONDS

    def dry_run_message(
        self,
        action: str,
        params: dict[str, Any],
        proposal_id: uuid.UUID,
        catalogue_version: str,
    ) -> dict[str, Any]:
        now = self.clock()
        message: dict[str, Any] = {
            "type": "dry_run",
            "request_id": str(uuid.uuid4()),
            "execution_id": None,
            "proposal_id": str(proposal_id),
            "action": action,
            "params": params,
            "state_fingerprint": None,
            "idempotency_key": None,
            "approved_by": None,
            "approved_at": None,
            "issued_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=REQUEST_TTL_SECONDS)),
            "catalogue_version": catalogue_version,
        }
        message["sig"] = sign(message, self.link_key)
        return message

    async def dry_run(
        self,
        action: str,
        params: dict[str, Any],
        proposal_id: uuid.UUID,
        catalogue_version: str,
    ) -> RunnerResult | None:
        """The verified ``dry_run_result``, or None after two attempts without one."""
        for attempt in (1, 2):
            message = self.dry_run_message(action, params, proposal_id, catalogue_version)
            try:
                result = await self._send_and_wait(message)
            except RedisError as exc:
                log.warning("dry run attempt %d failed: %s", attempt, type(exc).__name__)
                continue
            if result is not None:
                return result
            log.warning("no dry run result within %.0f s (attempt %d)", self.wait_seconds, attempt)
        return None

    async def _send_and_wait(self, message: dict[str, Any]) -> RunnerResult | None:
        latest: Any = await self.redis.xrevrange(RESULTS_STREAM, count=1)
        cursor = str(latest[0][0]) if latest else "0-0"
        await self.redis.xadd(
            REQUESTS_STREAM, {STREAM_FIELD: json.dumps(message, separators=(",", ":"))}
        )
        deadline = time.monotonic() + self.wait_seconds
        while (remaining := deadline - time.monotonic()) > 0:
            reply: Any = await self.redis.xread(
                {RESULTS_STREAM: cursor}, count=50, block=max(1, int(min(remaining, 1.0) * 1000))
            )
            for _, entries in reply or []:
                for entry_id, fields in entries:
                    cursor = str(entry_id)
                    result = self._accept(fields, message["request_id"])
                    if result is not None:
                        return result
        return None

    def _accept(self, fields: dict[str, str], request_id: str) -> RunnerResult | None:
        try:
            raw = json.loads(fields[STREAM_FIELD])
        except (KeyError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("request_id") != request_id:
            return None
        if raw.get("type") != "dry_run_result" or not verify(raw, self.link_key):
            log.warning("ignored a runner result with a bad signature or type")
            return None
        try:
            return RunnerResult.model_validate(raw)
        except ValidationError:
            log.warning("ignored a malformed runner result")
            return None
