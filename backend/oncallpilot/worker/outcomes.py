"""What happens after an approval (architecture §5.10 steps 6–8, §5.11; FR-013…FR-017, FR-024).

``Outcomes`` changes executions, proposals and incidents in response to:

- a verified runner result (``progress`` or a final ``execution_result``);
- the signal verifier's verdict (pass → resolved; fail → ``action_failed``);
- the sweeper (expired proposals, executions with no final result in time).

Final runner results, by status:

=========================== ============ ============ ==========================
result                      execution    proposal     incident
=========================== ============ ============ ==========================
``succeeded``               succeeded    succeeded    ``verifying``
``failed``                  failed       failed       ``action_failed`` → rollback
``aborted`` + STATE_DRIFT   aborted      superseded   ``awaiting_approval`` (new)
``refused`` / other abort   failed       failed       ``escalated`` runner_refused
=========================== ============ ============ ==========================

A result for an execution that has already ended changes nothing. A failed
action with a rollback step gets a ``kind=rollback`` proposal (new dry run,
re-approval); a failed rollback escalates ``rolled_back_after_failed_action``.
A failed action without a rollback step stays ``action_failed`` for a human
(the escalation reasons of C1 have no value for it; see ``handoff.md``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import asyncpg
from oncallpilot_contracts.domain import FINAL_EXECUTION_STATUSES
from oncallpilot_contracts.enums import EscalationReason
from oncallpilot_contracts.runner import RunnerResult
from redis.asyncio import Redis
from redis.exceptions import RedisError

from oncallpilot.agent.proposer import PreparedProposal, Refusal
from oncallpilot.approvals.limits import cooldown_key
from oncallpilot.catalogue import Catalogue, CatalogueError, rollback_step
from oncallpilot.db import agent as incident_db
from oncallpilot.db import approvals as approvals_db
from oncallpilot.db import executions as db
from oncallpilot.escalation import escalate_incident
from oncallpilot.events import EventStream
from oncallpilot.proposal_store import (
    PROPOSAL_TTL_SECONDS,
    ProposalText,
    iso,
    publish,
    store_proposal,
    text_from,
)
from oncallpilot.transitions import IllegalStatusChangeError, check_transition

log = logging.getLogger(__name__)

COOLDOWN_SECONDS = 120


class ActionPreparer(Protocol):
    async def prepare_action(
        self, action: str, params: dict[str, Any]
    ) -> PreparedProposal | Refusal: ...


@dataclass(frozen=True)
class _Change:
    """Events to publish after a committed change."""

    incident_id: UUID
    events: list[tuple[str, int, dict[str, Any]]]


@dataclass
class Outcomes:
    pool: asyncpg.Pool
    events: EventStream
    redis: Redis
    proposer: ActionPreparer
    catalogue_path: Path
    clock: Callable[[], datetime]
    proposal_ttl_seconds: int = PROPOSAL_TTL_SECONDS

    # --- runner results ---------------------------------------------------------------

    async def progress(self, result: RunnerResult) -> None:
        """One step of a running execution → ``execution.progress`` (RUN-013)."""
        assert result.execution_id is not None and result.step is not None
        async with self.pool.acquire() as conn, conn.transaction():
            row = await db.lock_execution(conn, result.execution_id)
            if row is None or row["status"] in FINAL_EXECUTION_STATUSES:
                log.info("progress for an unknown or ended execution ignored")
                return
            step = result.step
            output = dict(row["output"] or {})
            output["steps"] = [
                *output.get("steps", []),
                {"at": iso(result.ts), "step": step.name, "status": step.status,
                 "message": step.message},
            ]  # fmt: skip
            await db.update_execution(
                conn, result.execution_id, status="running", output=output, started_at=result.ts
            )
            if row["status"] == "queued":
                await incident_db.audit(
                    conn, "runner", "execution.started", row["incident_id"], "execution",
                    result.execution_id, {"proposal_id": str(row["proposal_id"])},
                )  # fmt: skip
            version = await incident_db.bump(conn, row["incident_id"], self.clock())
        await publish(
            self.events, "execution.progress", row["incident_id"], version, self.clock(),
            {"execution_id": str(result.execution_id), "proposal_id": str(row["proposal_id"]),
             "step": step.name, "status": step.status, "message": step.message},
        )  # fmt: skip

    async def final(self, result: RunnerResult) -> None:
        assert result.execution_id is not None
        if result.status == "succeeded":
            await self._succeeded(result)
        elif result.status == "failed":
            row = await self._failed(result)
            if row is not None:
                await self.offer_rollback(row, result.captured or {})
        elif result.status == "aborted" and result.error_code == "STATE_DRIFT":
            row = await self._drifted(result)
            if row is not None:
                await self.repropose(row)
        else:
            await self._refused(result)

    async def _succeeded(self, result: RunnerResult) -> None:
        assert result.execution_id is not None
        now = self.clock()
        structural = (
            result.health_after.structural.model_dump(mode="json") if result.health_after else None
        )
        async with self.pool.acquire() as conn, conn.transaction():
            row = await self._open_execution(conn, result.execution_id)
            if row is None:
                return
            output = dict(row["output"] or {}) | {"captured": result.captured or {}}
            await db.update_execution(
                conn, result.execution_id, status="succeeded", output=output,
                health_after={"structural": structural, "signals": None, "verdict": None},
                started_at=result.ts, finished_at=result.ts,
            )  # fmt: skip
            change = await self._settle(
                conn, row, "succeeded", "verifying", None, now,
                {"status": "succeeded", "error_code": None},
            )  # fmt: skip
        await self._cooldown(row["service"])
        await self._publish(change, now)

    async def _failed(self, result: RunnerResult) -> Any:
        assert result.execution_id is not None
        now = self.clock()
        health: dict[str, Any] | None = None
        if result.health_after is not None:
            structural = result.health_after.structural
            health = {
                "structural": structural.model_dump(mode="json"),
                "signals": None,
                "verdict": None if structural.passed else "fail",
            }
        async with self.pool.acquire() as conn, conn.transaction():
            row = await self._open_execution(conn, result.execution_id)
            if row is None:
                return None
            output = dict(row["output"] or {}) | {"captured": result.captured or {}}
            await db.update_execution(
                conn, result.execution_id, status="failed", output=output,
                error_code=result.error_code, health_after=health,
                started_at=result.ts, finished_at=result.ts,
            )  # fmt: skip
            change = await self._settle(
                conn, row, "failed", "action_failed", None, now,
                {"status": "failed", "error_code": result.error_code},
            )  # fmt: skip
        await self._cooldown(row["service"])
        await self._publish(change, now)
        return row

    async def _drifted(self, result: RunnerResult) -> Any:
        """``STATE_DRIFT``: the runner changed nothing (RUN-008)."""
        assert result.execution_id is not None
        async with self.pool.acquire() as conn, conn.transaction():
            row = await self._open_execution(conn, result.execution_id)
            if row is None:
                return None
            await db.update_execution(
                conn, result.execution_id, status="aborted", output=dict(row["output"] or {}),
                error_code="STATE_DRIFT", started_at=result.ts, finished_at=result.ts,
            )  # fmt: skip
            await incident_db.audit(
                conn, "runner", "execution.finished", row["incident_id"], "execution",
                result.execution_id, {"status": "aborted", "error_code": "STATE_DRIFT"},
            )  # fmt: skip
        return row

    async def _refused(self, result: RunnerResult) -> None:
        """``refused`` or a non-drift abort: nothing changed, so no rollback (FR-024)."""
        assert result.execution_id is not None
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            row = await self._open_execution(conn, result.execution_id)
            if row is None:
                return
            await db.update_execution(
                conn, result.execution_id, status="failed", output=dict(row["output"] or {}),
                error_code=result.error_code, finished_at=result.ts,
            )  # fmt: skip
            change = await self._settle(
                conn, row, "failed", "escalated", "runner_refused", now,
                {"status": result.status, "error_code": result.error_code},
            )  # fmt: skip
        await self._publish(change, now)

    # --- after a failure ------------------------------------------------------------

    async def offer_rollback(self, row: Any, captured: dict[str, Any]) -> None:
        """FR-015, FR-016: a rollback proposal, or escalation after a failed rollback."""
        incident_id = row["incident_id"]
        if row["kind"] == "rollback":
            await self._escalate(incident_id, "rolled_back_after_failed_action")
            return
        step = self._rollback_step(row, captured)
        if step is None:
            log.warning(
                "failed action has no rollback step; left action_failed for a human",
                extra={"incident_id": incident_id, "proposal_id": row["proposal_id"]},
            )
            return
        prepared = await self.proposer.prepare_action(step["action"], step["params"])
        if isinstance(prepared, Refusal):
            reason: EscalationReason = (
                "proposal_invalid" if prepared.outcome == "invalid" else "dry_run_unavailable"
            )
            await self._escalate(incident_id, reason)
            return
        async with self.pool.acquire() as conn:
            parent = await text_from(conn, row["proposal_id"])
        text = ProposalText(
            expected_effect=f"Undo the failed {row['action']}: back to the state before it",
            rationale=(
                f"The approved {row['action']} failed its health check; this rollback restores"
                " what the runner captured before acting."
            ),
            evidence_ids=parent.evidence_ids,
            evidence_refs=parent.evidence_refs,
            hypothesis_id=None,
        )
        await store_proposal(
            self.pool, self.events, incident_id, prepared, text, self.clock(),
            kind="rollback", parent_id=row["proposal_id"],
            ttl_seconds=self.proposal_ttl_seconds, actor="system",
        )  # fmt: skip

    def _rollback_step(self, row: Any, captured: dict[str, Any]) -> dict[str, Any] | None:
        """From the catalogue and what the runner captured; else the stored step."""
        try:
            spec = Catalogue.load(self.catalogue_path).actions.get(row["action"])
        except CatalogueError as exc:
            log.error("catalogue unreadable for the rollback step: %s", exc)
            spec = None
        if spec is not None and captured:
            step = rollback_step(spec, dict(row["params"]), captured)
            if step is not None:
                return step
        stored: dict[str, Any] | None = row["rollback_step"]
        return stored

    async def repropose(self, row: Any) -> None:
        """FR-017: a fresh dry run and a new proposal with the same action and parameters."""
        incident_id = row["incident_id"]
        prepared = await self.proposer.prepare_action(row["action"], dict(row["params"]))
        if isinstance(prepared, Refusal):
            now = self.clock()
            async with self.pool.acquire() as conn:
                await approvals_db.set_proposal_status(
                    conn, row["proposal_id"], "failed", "state drift; no fresh dry run", now
                )
            reason: EscalationReason = (
                "proposal_invalid" if prepared.outcome == "invalid" else "dry_run_unavailable"
            )
            await self._escalate(incident_id, reason)
            return
        async with self.pool.acquire() as conn:
            text = await text_from(conn, row["proposal_id"])
        await store_proposal(
            self.pool, self.events, incident_id, prepared, text, self.clock(),
            kind=row["kind"], parent_id=row["parent_proposal_id"],
            supersedes=row["proposal_id"], ttl_seconds=self.proposal_ttl_seconds,
            actor="system",
        )  # fmt: skip

    # --- verification verdicts (FR-014, FR-016) -------------------------------------

    async def verified(self, execution_id: UUID, signals: dict[str, Any], passed: bool) -> None:
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            row = await db.lock_execution(conn, execution_id)
            if row is None or row["incident_status"] != "verifying":
                return
            health = dict(row["health_after"] or {})
            health["signals"] = signals
            health["verdict"] = "pass" if passed else "fail"
            await db.set_health(conn, execution_id, health, dict(row["output"] or {}))
            await incident_db.audit(
                conn, "system", "health.verified", row["incident_id"], "execution", execution_id,
                {"verdict": health["verdict"], "signals": signals["signals"]},
            )  # fmt: skip
            if passed:
                resolution = "rolled_back" if row["kind"] == "rollback" else "action_succeeded"
                await db.resolve(conn, row["incident_id"], resolution, now)
                version = await incident_db.bump(conn, row["incident_id"], now)
                await incident_db.audit(
                    conn, "system", "incident.resolved", row["incident_id"], "incident",
                    row["incident_id"], {"resolution": resolution},
                )  # fmt: skip
                change = _Change(
                    row["incident_id"],
                    [("incident.resolved", version,
                      {"resolution": resolution, "resolved_at": iso(now)})],
                )  # fmt: skip
            elif row["kind"] == "rollback":
                change = await self._move(
                    conn, row, "escalated", "rolled_back_after_failed_action", now
                )
            else:
                change = await self._move(conn, row, "action_failed", None, now)
        await self._publish(change, now)
        if not passed and row["kind"] != "rollback":
            await self.offer_rollback(row, dict((row["output"] or {}).get("captured") or {}))

    # --- sweeper (FR-013, FR-024) ---------------------------------------------------

    async def expire(self, proposal_id: UUID) -> None:
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            p = await db.lock_pending_proposal(conn, proposal_id)
            if p is None or p["status"] != "pending" or p["expires_at"] > now:
                return
            await approvals_db.set_proposal_status(conn, proposal_id, "expired", None, now)
            proposal_version = await incident_db.bump(conn, p["incident_id"], now)
            await incident_db.audit(
                conn, "system", "proposal.expired", p["incident_id"], "proposal", proposal_id, {}
            )
            events = [
                ("proposal.updated", proposal_version, _proposal_data(proposal_id, "expired"))
            ]
            try:
                check_transition(p["incident_status"], "escalated")
            except IllegalStatusChangeError:
                pass
            else:
                await incident_db.set_status(
                    conn, p["incident_id"], "escalated", "proposal_expired", now
                )
                version = await incident_db.bump(conn, p["incident_id"], now)
                await incident_db.audit(
                    conn, "system", "incident.status_changed", p["incident_id"], "incident",
                    p["incident_id"], {"from": p["incident_status"], "to": "escalated",
                                       "reason": "proposal_expired"},
                )  # fmt: skip
                events.append(
                    ("incident.updated", version,
                     {"status": "escalated", "status_reason": "proposal_expired",
                      "severity": p["severity"]})
                )  # fmt: skip
        await self._publish(_Change(p["incident_id"], events), now)
        log.info("proposal expired", extra={"proposal_id": proposal_id})

    async def time_out(self, execution_id: UUID) -> None:
        """No final result in time: the runner may have acted, so no retry and no rollback."""
        now = self.clock()
        async with self.pool.acquire() as conn, conn.transaction():
            row = await db.lock_execution(conn, execution_id)
            if row is None or row["status"] not in ("queued", "running"):
                return
            await db.update_execution(
                conn, execution_id, status="failed", output=dict(row["output"] or {}),
                error_code="RUNNER_TIMEOUT", finished_at=now,
            )  # fmt: skip
            change = await self._settle(
                conn, row, "failed", "escalated", "runner_timeout", now,
                {"status": "failed", "error_code": "RUNNER_TIMEOUT"},
            )  # fmt: skip
        await self._publish(change, now)
        log.warning("execution timed out", extra={"execution_id": execution_id})

    # --- helpers --------------------------------------------------------------------

    async def _open_execution(self, conn: Any, execution_id: UUID) -> Any:
        row = await db.lock_execution(conn, execution_id)
        if row is None:
            log.warning("result for an unknown execution ignored")
            return None
        if row["status"] in FINAL_EXECUTION_STATUSES:
            log.info(
                "result for an execution that already ended ignored",
                extra={"execution_id": execution_id},
            )
            return None
        return row

    async def _settle(
        self,
        conn: Any,
        row: Any,
        proposal_status: str,
        incident_status: str,
        reason: EscalationReason | None,
        now: datetime,
        finished: dict[str, Any],
    ) -> _Change:
        """Execution audit, proposal status, incident status: the common tail."""
        await incident_db.audit(
            conn, "runner", "execution.finished", row["incident_id"], "execution", row["id"],
            finished,
        )  # fmt: skip
        await approvals_db.set_proposal_status(conn, row["proposal_id"], proposal_status, None, now)
        proposal_version = await incident_db.bump(conn, row["incident_id"], now)
        moved = await self._move(conn, row, incident_status, reason, now)
        return _Change(
            row["incident_id"],
            [("proposal.updated", proposal_version,
              _proposal_data(row["proposal_id"], proposal_status)), *moved.events],
        )  # fmt: skip

    async def _move(
        self, conn: Any, row: Any, status: str, reason: str | None, now: datetime
    ) -> _Change:
        current = row["incident_status"]
        try:
            check_transition(current, status)  # type: ignore[arg-type]
        except IllegalStatusChangeError:
            log.warning(
                "incident is %s; not moved to %s", current, status,
                extra={"incident_id": row["incident_id"]},
            )  # fmt: skip
            return _Change(row["incident_id"], [])
        await incident_db.set_status(conn, row["incident_id"], status, reason, now)
        version = await incident_db.bump(conn, row["incident_id"], now)
        payload: dict[str, Any] = {"from": current, "to": status}
        if reason:
            payload["reason"] = reason
        await incident_db.audit(
            conn, "system", "incident.status_changed", row["incident_id"], "incident",
            row["incident_id"], payload,
        )  # fmt: skip
        return _Change(
            row["incident_id"],
            [("incident.updated", version,
              {"status": status, "status_reason": reason, "severity": row["severity"]})],
        )  # fmt: skip

    async def _escalate(self, incident_id: UUID, reason: EscalationReason) -> None:
        await escalate_incident(
            self.pool, self.events, incident_id, reason, self.clock(), actor="system"
        )

    async def _cooldown(self, service: str) -> None:
        try:
            await self.redis.set(cooldown_key(service), "1", ex=COOLDOWN_SECONDS)
        except RedisError:
            log.error("could not set the cooldown for %s", service)

    async def _publish(self, change: _Change, now: datetime) -> None:
        for event, version, data in change.events:
            await publish(self.events, event, change.incident_id, version, now, data)


def _proposal_data(proposal_id: UUID, status: str) -> dict[str, Any]:
    return {
        "proposal_id": str(proposal_id),
        "status": status,
        "status_reason": None,
        "superseded_by": None,
    }


def timed_out_before(now: datetime, timeout_seconds: float) -> datetime:
    return now - timedelta(seconds=timeout_seconds)
