"""Single-use approval challenges in Redis (C8 §3; SEC-012).

``ocp:challenge:{challenge_id} = {user_id, proposal_id, nonce_hash, fingerprint}``
with a 120 s TTL, consumed with ``GETDEL``. Only the nonce's SHA-256 is stored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from oncallpilot_contracts.approval import CHALLENGE_TTL_SECONDS
from redis.asyncio import Redis


def challenge_key(challenge_id: uuid.UUID) -> str:
    return f"ocp:challenge:{challenge_id}"


def nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Challenge:
    user_id: str
    proposal_id: str
    nonce_hash: str
    fingerprint: str

    def matches(self, user_id: uuid.UUID, proposal_id: uuid.UUID, nonce: str) -> bool:
        return (
            self.user_id == str(user_id)
            and self.proposal_id == str(proposal_id)
            and hmac.compare_digest(self.nonce_hash, nonce_hash(nonce))
        )


@dataclass(frozen=True)
class IssuedChallenge:
    challenge_id: uuid.UUID
    nonce: str
    expires_at: datetime


class ChallengeStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def issue(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, fingerprint: str, now: datetime
    ) -> IssuedChallenge:
        challenge_id = uuid.uuid4()
        nonce = secrets.token_hex(32)
        value = {
            "user_id": str(user_id),
            "proposal_id": str(proposal_id),
            "nonce_hash": nonce_hash(nonce),
            "fingerprint": fingerprint,
        }
        await self._redis.set(
            challenge_key(challenge_id), json.dumps(value), ex=CHALLENGE_TTL_SECONDS
        )
        return IssuedChallenge(challenge_id, nonce, now + timedelta(seconds=CHALLENGE_TTL_SECONDS))

    async def consume(self, challenge_id: uuid.UUID) -> Challenge | None:
        """The challenge, deleted in the same command; None if unknown, used, or expired."""
        raw: Any = await self._redis.getdel(challenge_key(challenge_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return Challenge(
                str(data["user_id"]),
                str(data["proposal_id"]),
                str(data["nonce_hash"]),
                str(data["fingerprint"]),
            )
        except (ValueError, KeyError, TypeError):
            return None
