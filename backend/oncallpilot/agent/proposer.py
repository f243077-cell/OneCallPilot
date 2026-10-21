"""Proposal validation and the dry run (architecture §5.6 steps 4–5; AI-018, RUN-007).

``RunnerProposer.prepare`` turns the model's ``ProposalOut`` into everything
the stored proposal needs, or a refusal:

1. Validate against the catalogue, the target allowlist and the deploy ledger
   (``catalogue.validate``). Risk tier, approval requirement and the rollback
   step come from the catalogue only. A file that cannot be read fails
   closed (``invalid``).
2. Ask the runner for a signed dry run (``RunnerLink``). A refusal for the
   action, target or parameters is ``invalid``; no result, or any other
   refusal, is ``dry_run_unavailable``.
3. Fill the rollback step from what the dry run saw (what the runner will
   capture: ``previous_release``, ``previous_replicas``).

Storing the proposal is ``DbEffects.propose``'s job.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from oncallpilot_contracts.domain import DryRunResult, RollbackStep
from oncallpilot_contracts.runner import RunnerResult
from oncallpilot_contracts.signing import canonical_json
from pydantic import ValidationError

from oncallpilot.agent.schemas import ProposalOut
from oncallpilot.catalogue import (
    Catalogue,
    CatalogueError,
    ParamValue,
    Targets,
    rollback_step,
    validate,
)

log = logging.getLogger(__name__)

# Runner refusals that mean the proposal itself is wrong, not that the runner is unwell.
INVALID_CODES = {"ACTION_NOT_ALLOWED", "TARGET_NOT_ALLOWED", "VALIDATION_ERROR"}


class DryRunLink(Protocol):
    """``runner_link.RunnerLink``; a fake in unit tests."""

    async def dry_run(
        self,
        action: str,
        params: dict[str, Any],
        proposal_id: uuid.UUID,
        catalogue_version: str,
    ) -> RunnerResult | None: ...


@dataclass(frozen=True)
class PreparedProposal:
    proposal_id: uuid.UUID
    action: str
    params: dict[str, ParamValue]
    risk_tier: str
    approval_requirement: str
    rollback_step: dict[str, Any] | None
    dry_run: DryRunResult
    dry_run_at: datetime
    catalogue_version: str


@dataclass(frozen=True)
class Refusal:
    outcome: Literal["invalid", "dry_run_unavailable"]
    problems: tuple[str, ...]


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def proposal_fingerprint(
    proposal_id: uuid.UUID,
    action: str,
    params: dict[str, ParamValue],
    risk_tier: str,
    state_fingerprint: str,
    dry_run_at: datetime,
) -> str:
    """C8 §2: ``sha256(canonical_json({proposal_id, action, params, risk_tier,
    state_fingerprint, dry_run_at}))``, lower-case hex."""
    body = canonical_json(
        {
            "proposal_id": str(proposal_id),
            "action": action,
            "params": params,
            "risk_tier": risk_tier,
            "state_fingerprint": state_fingerprint,
            "dry_run_at": _iso(dry_run_at),
        }
    )
    return hashlib.sha256(body).hexdigest()


def captured_from(dry_run: DryRunResult) -> dict[str, ParamValue]:
    """The values the runner will capture, as the dry run saw them."""
    captured: dict[str, ParamValue] = {}
    if dry_run.current.active_release is not None:
        captured["previous_release"] = dry_run.current.active_release
    if dry_run.current.running_replicas is not None:
        captured["previous_replicas"] = dry_run.current.running_replicas
    return captured


@dataclass
class RunnerProposer:
    catalogue_path: Path
    targets_path: Path
    ledger_path: Path
    link: DryRunLink
    new_id: Callable[[], uuid.UUID] = uuid.uuid4

    async def prepare(self, proposal: ProposalOut) -> PreparedProposal | Refusal:
        return await self.prepare_action(proposal.action, proposal.params)

    async def prepare_action(
        self, action: str, params: dict[str, Any]
    ) -> PreparedProposal | Refusal:
        """The same validation and dry run for a rollback or a re-proposal after drift."""
        try:
            catalogue = Catalogue.load(self.catalogue_path)
            targets = Targets.load(self.targets_path)
            valid, problems = validate(catalogue, targets, self.ledger_path, action, params)
        except CatalogueError as exc:
            log.error("proposal validation could not run: %s", exc)
            return Refusal("invalid", ("the catalogue or the target allowlist is unreadable",))
        if valid is None:
            return Refusal("invalid", tuple(problems))

        proposal_id = self.new_id()
        result = await self.link.dry_run(
            valid.action.name, dict(valid.params), proposal_id, valid.catalogue_version
        )
        if result is None:
            return Refusal("dry_run_unavailable", ("no dry run result after a retry",))
        if result.status != "ok" or result.dry_run is None:
            code = result.error_code or "UNKNOWN"
            outcome: Literal["invalid", "dry_run_unavailable"] = (
                "invalid" if code in INVALID_CODES else "dry_run_unavailable"
            )
            return Refusal(outcome, (f"the runner refused the dry run: {code}",))

        step = rollback_step(valid.action, valid.params, captured_from(result.dry_run))
        if step is not None:
            try:
                RollbackStep.model_validate(step)
            except ValidationError:
                log.warning("rollback step does not validate; stored as not reversible")
                step = None
        return PreparedProposal(
            proposal_id=proposal_id,
            action=valid.action.name,
            params=dict(valid.params),
            risk_tier=valid.action.risk_tier,
            approval_requirement=valid.action.approval_requirement,
            rollback_step=step,
            dry_run=result.dry_run,
            dry_run_at=result.ts,
            catalogue_version=valid.catalogue_version,
        )
