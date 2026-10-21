"""Challenge, approve and reject, with the checks in the order of C8 (architecture §5.9;
API-008…API-010, SEC-001, SEC-012, SEC-017, TEST-005).

Approve:
0. valid JWT (the router), proposal exists, ``Idempotency-Key`` present;
1. key seen → replay the stored response; same key, other body → conflict;
2. challenge valid for this user and proposal, consumed with ``GETDEL``;
3. fingerprint matches; 4. proposal pending and not expired;
5. medium/high tier → biometric; 6. rate limit; 7. cooldown (rollbacks exempt).
Then one transaction (approval, execution, statuses, audit), and after the
commit the signed ``execute`` request.

A check that cannot be evaluated (Redis down) refuses: nothing is approved.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg
from oncallpilot_contracts.approval import (
    ApproveRequest,
    ChallengeRequest,
    ChallengeResponse,
    RejectRequest,
)
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from oncallpilot.api.errors import (
    ApiError,
    BiometricRequiredError,
    ChallengeInvalidError,
    ChecksUnavailableError,
    CooldownActiveError,
    NotFoundError,
    ProposalExpiredError,
    ProposalNotPendingError,
    RateLimitedError,
    StaleProposalError,
    ValidationFailedError,
    error_body,
)
from oncallpilot.approvals import limits
from oncallpilot.approvals.challenges import ChallengeStore
from oncallpilot.approvals.execute import execute_message, send_execute
from oncallpilot.approvals.idempotency import IdempotencyStore, parse_key, request_hash
from oncallpilot.db import agent as incident_db
from oncallpilot.db import approvals as db
from oncallpilot.events import EventStream
from oncallpilot.transitions import IllegalStatusChangeError, check_transition

log = logging.getLogger(__name__)

BIOMETRIC_TIERS = {"medium", "high"}


def _parse[B: BaseModel](model: type[B], raw: bytes) -> B:
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        errors = [
            {"loc": ["body", *(str(p) for p in err.get("loc", ()))], "msg": str(err.get("msg"))}
            for err in exc.errors()
        ]
        raise ValidationFailedError("invalid request", {"errors": errors}) from exc


def _check_pending(status: str, expires_at: datetime, now: datetime) -> None:
    """``expired`` (swept, or past ``expires_at``) → PROPOSAL_EXPIRED (FR-013); any other
    decided status → PROPOSAL_NOT_PENDING."""
    if status == "expired" or (status == "pending" and expires_at <= now):
        raise ProposalExpiredError()
    if status != "pending":
        raise ProposalNotPendingError()


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any]


@dataclass
class Approvals:
    pool: asyncpg.Pool
    redis: Redis
    events: EventStream
    action_signing_key: bytes
    clock: Callable[[], datetime]
    idempotency_wait_seconds: float = 10.0

    @property
    def _challenges(self) -> ChallengeStore:
        return ChallengeStore(self.redis)

    @property
    def _idempotency(self) -> IdempotencyStore:
        return IdempotencyStore(self.redis, self.idempotency_wait_seconds)

    # --- challenge ----------------------------------------------------------------

    async def challenge(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, raw: bytes
    ) -> ChallengeResponse:
        async with self.pool.acquire() as conn:
            p = await db.get_proposal(conn, proposal_id)
        if p is None:
            raise NotFoundError("proposal not found")
        body = _parse(ChallengeRequest, raw)
        now = self.clock()
        _check_pending(p["status"], p["expires_at"], now)
        if body.proposal_fingerprint != p["fingerprint"]:
            raise StaleProposalError()
        try:
            issued = await self._challenges.issue(user_id, proposal_id, p["fingerprint"], now)
        except RedisError as exc:
            raise ChecksUnavailableError() from exc
        async with self.pool.acquire() as conn:
            await incident_db.audit(
                conn, f"user:{user_id}", "challenge.issued", p["incident_id"], "proposal",
                proposal_id, {"challenge_id": str(issued.challenge_id),
                              "fingerprint": p["fingerprint"]},
            )  # fmt: skip
        return ChallengeResponse(
            challenge_id=issued.challenge_id,
            nonce=issued.nonce,
            expires_at=issued.expires_at,
            approval_requirement=p["approval_requirement"],
        )

    # --- idempotent operations ------------------------------------------------------

    async def approve(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, key_header: str | None, raw: bytes
    ) -> Response:
        return await self._idempotent(
            "approve", user_id, proposal_id, key_header, raw,
            lambda key: self._approve(user_id, proposal_id, key, raw),
        )  # fmt: skip

    async def reject(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, key_header: str | None, raw: bytes
    ) -> Response:
        return await self._idempotent(
            "reject", user_id, proposal_id, key_header, raw,
            lambda key: self._reject(user_id, proposal_id, key, raw),
        )  # fmt: skip

    async def _idempotent(
        self,
        op: str,
        user_id: uuid.UUID,
        proposal_id: uuid.UUID,
        key_header: str | None,
        raw: bytes,
        run: Callable[[uuid.UUID], Awaitable[Response]],
    ) -> Response:
        # Check 0: the proposal exists, then the key is present.
        async with self.pool.acquire() as conn:
            if await db.get_proposal(conn, proposal_id) is None:
                raise NotFoundError("proposal not found")
        key = parse_key(key_header)
        body_hash = request_hash(proposal_id, raw)
        store = self._idempotency
        # Check 1: replay or conflict.
        try:
            stored = await store.begin(op, user_id, key, body_hash)
        except RedisError as exc:
            raise ChecksUnavailableError() from exc
        if stored is not None:
            return Response(stored.status, stored.body)
        try:
            response = await run(key)
        except ApiError as exc:
            response = Response(exc.status_code, error_body(exc))
        except BaseException:
            # Unexpected: free the key. A retry is still safe: the challenge is consumed.
            try:
                await store.release(op, user_id, key)
            except RedisError:
                log.error("could not release idempotency key after an error")
            raise
        try:
            await store.finish(op, user_id, key, body_hash, response.status, response.body)
        except RedisError:
            log.error("could not store the response for an idempotency key")
        return response

    # --- approve --------------------------------------------------------------------

    async def _approve(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, key: uuid.UUID, raw: bytes
    ) -> Response:
        body = _parse(ApproveRequest, raw)
        # Check 2: the challenge, consumed whatever happens next.
        try:
            challenge = await self._challenges.consume(body.challenge_id)
        except RedisError as exc:
            raise ChecksUnavailableError() from exc
        if challenge is None or not challenge.matches(user_id, proposal_id, body.nonce):
            raise ChallengeInvalidError()
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            p = await db.lock_proposal(conn, proposal_id)
            # Check 3: the fingerprint the engineer saw, the challenge's, and the stored one.
            if body.proposal_fingerprint != challenge.fingerprint or (
                body.proposal_fingerprint != p["fingerprint"]
            ):
                raise StaleProposalError()
            # Check 4.
            _check_pending(p["status"], p["expires_at"], now)
            # Check 5.
            if p["risk_tier"] in BIOMETRIC_TIERS and body.auth_method != "biometric":
                raise BiometricRequiredError()
            # Checks 6 and 7.
            try:
                wait = await limits.rate_limit_retry_after(self.redis, p["service"], now)
                if wait is not None:
                    raise RateLimitedError(wait)
                if p["kind"] != "rollback":
                    wait = await limits.cooldown_retry_after(self.redis, p["service"])
                    if wait is not None:
                        raise CooldownActiveError(wait)
            except RedisError as exc:
                raise ChecksUnavailableError() from exc
            try:
                check_transition(p["incident_status"], "executing")
            except IllegalStatusChangeError as exc:
                raise ProposalNotPendingError() from exc

            device_id = (
                await db.device_of_user(conn, body.device_id, user_id) if body.device_id else None
            )
            approval_id = await db.insert_approval(
                conn, proposal_id=proposal_id, user_id=user_id, decision="approved",
                reason=None, auth_method=body.auth_method, challenge_id=body.challenge_id,
                device_id=device_id, idempotency_key=key, now=now,
            )  # fmt: skip
            execution_id = await db.insert_execution(conn, proposal_id, approval_id, now)
            await db.set_proposal_status(conn, proposal_id, "approved", None, now)
            proposal_version = await incident_db.bump(conn, p["incident_id"], now)
            await incident_db.set_status(conn, p["incident_id"], "executing", None, now)
            incident_version = await incident_db.bump(conn, p["incident_id"], now)
            top = await db.top_hypothesis(conn, p["incident_id"])
            actor = f"user:{user_id}"
            # SEC-017: what the engineer saw.
            await incident_db.audit(
                conn, actor, "approval.approved", p["incident_id"], "proposal", proposal_id, {
                    "approval_id": str(approval_id),
                    "fingerprint": p["fingerprint"],
                    "dry_run_result": p["dry_run_result"],
                    "top_hypothesis": {
                        "summary": top["summary"] if top else None,
                        "confidence": float(top["confidence"]) if top else None,
                    },
                    "evidence_refs": await db.evidence_refs(conn, p["evidence_ids"]),
                    "auth_method": body.auth_method,
                    "device_id": str(body.device_id) if body.device_id else None,
                },
            )  # fmt: skip
            await incident_db.audit(
                conn, actor, "execution.queued", p["incident_id"], "execution", execution_id,
                {"proposal_id": str(proposal_id), "action": p["action"], "params": p["params"]},
            )  # fmt: skip
            await incident_db.audit(
                conn, actor, "incident.status_changed", p["incident_id"], "incident",
                p["incident_id"], {"from": p["incident_status"], "to": "executing"},
            )  # fmt: skip

        try:
            await limits.record_execution(self.redis, p["service"], execution_id, now)
        except RedisError:
            log.error("could not record the execution for the rate limit")
        await self._publish(
            "proposal.updated", p["incident_id"], proposal_version, now,
            {"proposal_id": str(proposal_id), "status": "approved", "status_reason": None,
             "superseded_by": None},
        )  # fmt: skip
        await self._publish(
            "incident.updated", p["incident_id"], incident_version, now,
            {"status": "executing", "status_reason": None, "severity": p["severity"]},
        )  # fmt: skip
        message = execute_message(
            execution_id=execution_id,
            proposal_id=proposal_id,
            action=p["action"],
            params=p["params"],
            state_fingerprint=p["dry_run_result"]["state_fingerprint"],
            idempotency_key=key,
            approved_by=user_id,
            approved_at=now,
            catalogue_version=p["catalogue_version"],
            key=self.action_signing_key,
        )
        try:
            await send_execute(self.redis, message)
        except RedisError:
            # Committed: the sweeper ends the execution as runner_timeout (§5.10 step 8).
            log.error(
                "could not send the execute request",
                extra={"execution_id": execution_id, "proposal_id": proposal_id},
            )
        log.info(
            "proposal approved", extra={"proposal_id": proposal_id, "execution_id": execution_id}
        )
        return Response(202, {"execution_id": str(execution_id), "status": "queued"})

    # --- reject ---------------------------------------------------------------------

    async def _reject(
        self, user_id: uuid.UUID, proposal_id: uuid.UUID, key: uuid.UUID, raw: bytes
    ) -> Response:
        body = _parse(RejectRequest, raw)
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            p = await db.lock_proposal(conn, proposal_id)
            if p["status"] != "pending":
                raise ProposalNotPendingError()
            try:
                check_transition(p["incident_status"], "escalated")
            except IllegalStatusChangeError as exc:
                raise ProposalNotPendingError() from exc
            approval_id = await db.insert_approval(
                conn, proposal_id=proposal_id, user_id=user_id, decision="rejected",
                reason=body.reason, auth_method="tap", challenge_id=None, device_id=None,
                idempotency_key=key, now=now,
            )  # fmt: skip
            await db.set_proposal_status(conn, proposal_id, "rejected", None, now)
            proposal_version = await incident_db.bump(conn, p["incident_id"], now)
            await incident_db.set_status(
                conn, p["incident_id"], "escalated", "proposal_rejected", now
            )
            incident_version = await incident_db.bump(conn, p["incident_id"], now)
            actor = f"user:{user_id}"
            await incident_db.audit(
                conn, actor, "approval.rejected", p["incident_id"], "proposal", proposal_id,
                {"approval_id": str(approval_id), "reason": body.reason,
                 "fingerprint": p["fingerprint"]},
            )  # fmt: skip
            await incident_db.audit(
                conn, actor, "incident.status_changed", p["incident_id"], "incident",
                p["incident_id"],
                {"from": p["incident_status"], "to": "escalated", "reason": "proposal_rejected"},
            )  # fmt: skip
        await self._publish(
            "proposal.updated", p["incident_id"], proposal_version, now,
            {"proposal_id": str(proposal_id), "status": "rejected", "status_reason": None,
             "superseded_by": None},
        )  # fmt: skip
        await self._publish(
            "incident.updated", p["incident_id"], incident_version, now,
            {"status": "escalated", "status_reason": "proposal_rejected",
             "severity": p["severity"]},
        )  # fmt: skip
        log.info("proposal rejected", extra={"proposal_id": proposal_id})
        return Response(200, {"proposal_id": str(proposal_id), "status": "rejected"})

    async def _publish(
        self, event: str, incident_id: uuid.UUID, version: int, ts: datetime, data: dict[str, Any]
    ) -> None:
        try:
            await self.events.publish(event, incident_id, version, ts, data)
        except RedisError as exc:
            log.error(
                "publish %s failed: %s", event, type(exc).__name__,
                extra={"incident_id": incident_id},
            )  # fmt: skip
