"""RunnerProposer: validation, dry run outcomes, rollback step, fingerprint (B2.8; AI-018,
RUN-007, C8 §2)."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from oncallpilot_contracts.runner import RunnerResult

from oncallpilot.agent.proposer import (
    PreparedProposal,
    Refusal,
    RunnerProposer,
    proposal_fingerprint,
)
from oncallpilot.agent.schemas import ProposalOut
from tests.agent.fakes import proposal
from tests.runner_replies import DRY_RUN_AT, STATE_FINGERPRINT, dry_run_reply
from tests.test_catalogue import ROOT, ledger

PROPOSAL_ID = uuid.UUID("8a2d3b5f-1c8e-4d2f-8b64-3e9f5a7b8c02")


class FakeLink:
    def __init__(self, reply: dict[str, Any] | None = None, **kwargs: Any) -> None:
        self.reply = reply
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict[str, Any], uuid.UUID, str]] = []

    async def dry_run(
        self, action: str, params: dict[str, Any], proposal_id: uuid.UUID, catalogue_version: str
    ) -> RunnerResult | None:
        self.calls.append((action, params, proposal_id, catalogue_version))
        if self.reply is None and not self.kwargs:
            return None
        return RunnerResult.model_validate(dry_run_reply(str(uuid.uuid4()), **self.kwargs))


def proposer(tmp_path: Path, link: FakeLink) -> RunnerProposer:
    return RunnerProposer(
        catalogue_path=ROOT / "contracts" / "actions.yaml",
        targets_path=ROOT / "runner" / "targets.yaml",
        ledger_path=ledger(tmp_path, "1.4.0", "1.5.0"),
        link=link,
        new_id=lambda: PROPOSAL_ID,
    )


def rollback(**overrides: Any) -> ProposalOut:
    return ProposalOut.model_validate(proposal() | overrides)


async def test_valid_proposal_is_prepared_from_the_catalogue_and_dry_run(tmp_path: Path) -> None:
    link = FakeLink(active="1.5.0")
    prepared = await proposer(tmp_path, link).prepare(rollback())
    assert isinstance(prepared, PreparedProposal)
    assert link.calls == [
        ("rollback_deploy", {"service": "api", "target_release": "1.4.0"}, PROPOSAL_ID, "1.0.0")
    ]
    assert (prepared.risk_tier, prepared.approval_requirement) == ("medium", "biometric")
    assert prepared.rollback_step == {
        "action": "rollback_deploy",
        "params": {"service": "api", "target_release": "1.5.0"},
    }
    assert prepared.dry_run_at == DRY_RUN_AT
    assert prepared.dry_run.state_fingerprint == STATE_FINGERPRINT


async def test_invalid_proposal_never_reaches_the_runner(tmp_path: Path) -> None:
    link = FakeLink(active="1.5.0")
    bad = rollback(params={"service": "api", "target_release": "9.9.9"})
    outcome = await proposer(tmp_path, link).prepare(bad)
    assert isinstance(outcome, Refusal) and outcome.outcome == "invalid"
    assert link.calls == []


async def test_disabled_action_is_invalid_even_past_the_schema(tmp_path: Path) -> None:
    # ProposalOut already refuses the name; the validator must refuse it on its own too.
    sneaked = rollback().model_copy(
        update={"action": "run_migration_rollback", "params": {"migration_id": "0042"}}
    )
    outcome = await proposer(tmp_path, FakeLink()).prepare(sneaked)
    assert isinstance(outcome, Refusal) and outcome.outcome == "invalid"


async def test_unreadable_catalogue_fails_closed(tmp_path: Path) -> None:
    p = proposer(tmp_path, FakeLink(active="1.5.0"))
    p.catalogue_path = tmp_path / "missing.yaml"
    outcome = await p.prepare(rollback())
    assert isinstance(outcome, Refusal) and outcome.outcome == "invalid"


async def test_no_dry_run_result_is_unavailable(tmp_path: Path) -> None:
    outcome = await proposer(tmp_path, FakeLink()).prepare(rollback())
    assert isinstance(outcome, Refusal) and outcome.outcome == "dry_run_unavailable"


async def test_runner_refusals_map_to_invalid_or_unavailable(tmp_path: Path) -> None:
    for code, expected in (
        ("TARGET_NOT_ALLOWED", "invalid"),
        ("VALIDATION_ERROR", "invalid"),
        ("ACTION_NOT_ALLOWED", "invalid"),
        ("COOLDOWN_ACTIVE", "dry_run_unavailable"),
        ("RATE_LIMITED", "dry_run_unavailable"),
    ):
        outcome = await proposer(tmp_path, FakeLink(error_code=code)).prepare(rollback())
        assert isinstance(outcome, Refusal) and outcome.outcome == expected, code
        assert code in outcome.problems[0]


async def test_rollback_step_needs_the_captured_value(tmp_path: Path) -> None:
    prepared = await proposer(tmp_path, FakeLink(active=None, replicas=None)).prepare(rollback())
    assert isinstance(prepared, PreparedProposal) and prepared.rollback_step is None


def test_fingerprint_is_sha256_of_the_canonical_fields() -> None:
    params: dict[str, str | int] = {"service": "api", "target_release": "1.4.0"}
    expected = hashlib.sha256(
        json.dumps(
            {
                "action": "rollback_deploy",
                "dry_run_at": "2026-10-21T10:04:58Z",
                "params": params,
                "proposal_id": str(PROPOSAL_ID),
                "risk_tier": "medium",
                "state_fingerprint": STATE_FINGERPRINT,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    got = proposal_fingerprint(
        PROPOSAL_ID, "rollback_deploy", params, "medium", STATE_FINGERPRINT, DRY_RUN_AT
    )
    assert got == expected
    other = proposal_fingerprint(
        PROPOSAL_ID, "rollback_deploy", params, "medium", "0" * 64, DRY_RUN_AT
    )
    assert other != expected
