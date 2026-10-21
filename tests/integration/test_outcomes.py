"""After approval: runner results, verification, rollback, drift, expiry and timeouts
(B2.10; architecture §5.10–§5.11; FR-013…FR-017, FR-024, RUN-015)."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from approval_env import FINGERPRINT, NOW, Env, make_pending
from oncallpilot_contracts.domain import DryRunResult
from oncallpilot_contracts.runner import RESULTS_STREAM, STREAM_FIELD
from oncallpilot_contracts.ws import WsServerMessage

from oncallpilot.agent.proposer import PreparedProposal, Refusal
from oncallpilot.approvals import limits
from oncallpilot.catalogue import Catalogue
from oncallpilot.db.incidents import IncidentRepository
from oncallpilot.detector.config import load_config, read_config
from oncallpilot.detector.engine import Samples
from oncallpilot.events import EventStream
from oncallpilot.worker.loops import LatestSamples, RunnerResultConsumer, SignalVerifier, Sweeper
from oncallpilot.worker.outcomes import Outcomes
from tests.runner_replies import ACTION_KEY, LINK_KEY, dry_run, execution_reply
from tests.test_detector_config import CONFIG

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = Catalogue.load(ROOT / "contracts" / "actions.yaml")


def result(env: Env, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """A signed runner result stamped with the test clock."""
    return execution_reply(*args, ts=env.clock[0], **kwargs)


class Preparer:
    def __init__(self, refuse: str | None = None) -> None:
        self.refuse = refuse
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def prepare_action(
        self, action: str, params: dict[str, Any]
    ) -> PreparedProposal | Refusal:
        self.calls.append((action, params))
        if self.refuse == "invalid":
            return Refusal("invalid", ("no",))
        if self.refuse == "dry_run_unavailable":
            return Refusal("dry_run_unavailable", ("no",))
        spec = CATALOGUE.actions[action]
        return PreparedProposal(
            proposal_id=uuid.uuid4(),
            action=action,
            params=params,
            risk_tier=spec.risk_tier,
            approval_requirement=spec.approval_requirement,
            rollback_step=None,
            dry_run=DryRunResult.model_validate(dry_run()),
            dry_run_at=NOW,
            catalogue_version="1.0.0",
        )


class Worker:
    def __init__(self, env: Env, preparer: Preparer) -> None:
        self.env = env
        self.preparer = preparer
        self.outcomes = Outcomes(
            pool=env.pool,
            events=EventStream(env.redis),
            redis=env.redis,
            proposer=preparer,
            catalogue_path=ROOT / "contracts" / "actions.yaml",
            clock=lambda: env.clock[0],
        )
        self.results = RunnerResultConsumer(env.redis, self.outcomes, LINK_KEY, "w1")
        self.latest = LatestSamples()
        self.verifier = SignalVerifier(
            env.pool, self.outcomes, load_config(read_config(CONFIG)), self.latest,
            lambda: env.clock[0],
        )  # fmt: skip
        self.sweeper = Sweeper(env.pool, self.outcomes, lambda: env.clock[0])

    async def deliver(self, reply: dict[str, Any]) -> None:
        await self.results.ensure_group()
        await self.env.redis.xadd(RESULTS_STREAM, {STREAM_FIELD: json.dumps(reply)})
        await self.results.poll(10)


@pytest.fixture
def worker(env: Env) -> Worker:
    return Worker(env, Preparer())


async def approve(env: Env, proposal_id: uuid.UUID | None = None) -> str:
    if proposal_id is not None:
        env.proposal_id = proposal_id
    fingerprint = await env.pool.fetchval(
        "select fingerprint from ocp.proposals where id = $1", env.proposal_id
    )
    challenged = await env.challenge(fingerprint)
    assert challenged.status_code == 200, challenged.text
    issued = challenged.json()
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": fingerprint,
        "auth_method": "biometric",
    }
    response = await env.approve(body=body, key=str(uuid.uuid4()))
    assert response.status_code == 202, response.text
    return str(response.json()["execution_id"])


async def state(env: Env) -> dict[str, Any]:
    row = await env.pool.fetchrow(
        "select status::text as status, status_reason, resolution::text as resolution"
        " from ocp.incidents where id = $1",
        env.incident_id,
    )
    assert row is not None
    proposals = await env.pool.fetch(
        "select id, kind::text as kind, status::text as status, parent_proposal_id,"
        " superseded_by, action, params from ocp.proposals where incident_id = $1"
        # The test clock is frozen: the parent (primary, or the superseded one) sorts first.
        " order by created_at, kind, superseded_by nulls last",
        env.incident_id,
    )
    executions = await env.pool.fetch(
        "select id, status::text as status, error_code, health_after, output"
        " from ocp.executions order by created_at, id"
    )
    return {"incident": dict(row), "proposals": proposals, "executions": executions}


async def detail_validates(env: Env) -> None:
    detail = await IncidentRepository(env.pool).get_detail(env.incident_id)
    assert detail is not None
    for event in await EventStream(env.redis).read_after("0-0", block_ms=None, count=200):
        WsServerMessage.model_validate(event)


async def test_succeeded_then_signals_normal_resolves(env: Env, worker: Worker) -> None:
    execution_id = await approve(env)
    await worker.deliver(result(env, execution_id, "running", step="validated"))
    await worker.deliver(
        result(env, execution_id, "succeeded", health=True, captured={"previous_release": "1.5.0"})
    )
    s = await state(env)
    assert s["incident"]["status"] == "verifying"
    assert [p["status"] for p in s["proposals"]] == ["succeeded"]
    execution = s["executions"][0]
    assert execution["status"] == "succeeded"
    assert execution["output"]["steps"][0]["step"] == "validated"
    assert execution["health_after"]["structural"]["passed"] is True
    assert await env.redis.ttl(limits.cooldown_key("api")) > 0

    worker.latest.update(env.clock[0], {"api": Samples(error_rate=0.001)})
    await worker.verifier.cycle()
    assert (await state(env))["incident"]["status"] == "verifying"  # not yet 60 s
    env.clock[0] += timedelta(seconds=61)
    worker.latest.update(env.clock[0], {"api": Samples(error_rate=0.002)})
    await worker.verifier.cycle()

    s = await state(env)
    assert (s["incident"]["status"], s["incident"]["resolution"]) == (
        "resolved",
        "action_succeeded",
    )
    assert s["executions"][0]["health_after"]["verdict"] == "pass"
    await detail_validates(env)
    events = await EventStream(env.redis).read_after("0-0", block_ms=None, count=200)
    assert events[-1]["event"] == "incident.resolved"
    assert "execution.progress" in [e["event"] for e in events]


async def test_verification_timeout_offers_a_rollback(env: Env, worker: Worker) -> None:
    execution_id = await approve(env)
    await worker.deliver(
        result(env, execution_id, "succeeded", health=True, captured={"previous_release": "1.5.0"})
    )
    worker.latest.update(env.clock[0], {"api": Samples(error_rate=0.3)})  # still broken
    env.clock[0] += timedelta(seconds=301)
    worker.latest.update(env.clock[0], {"api": Samples(error_rate=0.3)})
    await worker.verifier.cycle()

    s = await state(env)
    assert s["incident"]["status"] == "awaiting_approval"
    assert s["executions"][0]["health_after"]["verdict"] == "fail"
    primary, rollback = s["proposals"]
    assert (rollback["kind"], rollback["status"], rollback["parent_proposal_id"]) == (
        "rollback",
        "pending",
        primary["id"],
    )
    assert worker.preparer.calls == [
        ("rollback_deploy", {"service": "api", "target_release": "1.5.0"})
    ]
    await detail_validates(env)


async def test_structural_failure_offers_rollback_then_rollback_resolves(
    env: Env,
    worker: Worker,
) -> None:
    execution_id = await approve(env)
    await worker.deliver(
        result(env, execution_id, "failed", health=False, captured={"previous_release": "1.5.0"})
    )
    s = await state(env)
    assert s["executions"][0]["status"] == "failed"
    rollback = s["proposals"][1]
    assert rollback["kind"] == "rollback"
    assert s["incident"]["status"] == "awaiting_approval"

    # FR-016: the rollback is approved, executes, and the signals recover → rolled_back.
    await env.redis.delete(limits.cooldown_key("api"))
    second = await approve(env, rollback["id"])
    await worker.deliver(result(env, second, "succeeded", health=True))
    worker.latest.update(env.clock[0], {"api": Samples(error_rate=0.001)})
    await worker.verifier.cycle()
    env.clock[0] += timedelta(seconds=61)
    worker.latest.update(env.clock[0], {"api": Samples(error_rate=0.001)})
    await worker.verifier.cycle()
    s = await state(env)
    assert (s["incident"]["status"], s["incident"]["resolution"]) == ("resolved", "rolled_back")
    await detail_validates(env)


async def test_failed_rollback_escalates(env: Env, worker: Worker) -> None:
    execution_id = await approve(env)
    await worker.deliver(
        result(env, execution_id, "failed", health=False, captured={"previous_release": "1.5.0"})
    )
    rollback = (await state(env))["proposals"][1]
    second = await approve(env, rollback["id"])
    await worker.deliver(result(env, second, "failed", health=False))
    s = await state(env)
    assert (s["incident"]["status"], s["incident"]["status_reason"]) == (
        "escalated",
        "rolled_back_after_failed_action",
    )
    assert len(s["proposals"]) == 2


async def test_failed_action_without_rollback_step_stays_action_failed(
    env: Env,
    worker: Worker,
) -> None:
    await env.pool.execute(
        "update ocp.proposals set status = 'superseded' where id = $1", env.proposal_id
    )
    low = await make_pending(env.pool, env.incident_id, "low")  # restart_service
    env.proposal_id = low
    issued = (await env.challenge()).json()
    body = {
        "challenge_id": issued["challenge_id"],
        "nonce": issued["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "tap",
    }
    response = await env.approve(body=body, key=str(uuid.uuid4()))
    assert response.status_code == 202
    await worker.deliver(result(env, response.json()["execution_id"], "failed", health=False))
    s = await state(env)
    assert s["incident"]["status"] == "action_failed"
    assert worker.preparer.calls == []


async def test_state_drift_supersedes_and_reproposes(env: Env, worker: Worker) -> None:
    execution_id = await approve(env)
    await worker.deliver(result(env, execution_id, "aborted", error_code="STATE_DRIFT"))
    s = await state(env)
    old, new = s["proposals"]
    assert (old["status"], old["superseded_by"]) == ("superseded", new["id"])
    assert (new["status"], new["action"], new["params"]) == (
        "pending",
        old["action"],
        old["params"],
    )
    assert (s["executions"][0]["status"], s["executions"][0]["error_code"]) == (
        "aborted",
        "STATE_DRIFT",
    )
    assert s["incident"]["status"] == "awaiting_approval"
    await detail_validates(env)


async def test_drift_without_a_fresh_dry_run_escalates(env: Env) -> None:
    worker = Worker(env, Preparer(refuse="dry_run_unavailable"))
    execution_id = await approve(env)
    await worker.deliver(result(env, execution_id, "aborted", error_code="STATE_DRIFT"))
    s = await state(env)
    assert (s["incident"]["status"], s["incident"]["status_reason"]) == (
        "escalated",
        "dry_run_unavailable",
    )


@pytest.mark.parametrize(
    ("status", "code"), [("refused", "REQUEST_EXPIRED"), ("aborted", "VALIDATION_ERROR")]
)
async def test_refused_or_aborted_escalates_without_rollback(
    env: Env,
    worker: Worker,
    status: str,
    code: str,
) -> None:
    execution_id = await approve(env)
    await worker.deliver(result(env, execution_id, status, error_code=code))
    s = await state(env)
    assert (s["incident"]["status"], s["incident"]["status_reason"]) == (
        "escalated",
        "runner_refused",
    )
    assert (s["executions"][0]["status"], s["executions"][0]["error_code"]) == ("failed", code)
    assert [p["status"] for p in s["proposals"]] == ["failed"]
    assert worker.preparer.calls == []


async def test_duplicate_late_and_forged_results_change_nothing(
    env: Env,
    worker: Worker,
) -> None:
    execution_id = await approve(env)
    # RUN-015: signed with the wrong key → dropped.
    await worker.deliver(result(env, execution_id, "succeeded", health=True, key=ACTION_KEY))
    assert (await state(env))["executions"][0]["status"] == "queued"
    await worker.deliver(result(env, execution_id, "succeeded", health=True))
    before = await state(env)
    await worker.deliver(result(env, execution_id, "failed", health=False))
    await worker.deliver(result(env, execution_id, "succeeded", health=True))
    assert await state(env) == before


async def test_sweeper_times_out_a_silent_runner(env: Env, worker: Worker) -> None:
    execution_id = await approve(env)
    env.clock[0] += timedelta(seconds=239)
    await worker.sweeper.cycle()
    assert (await state(env))["incident"]["status"] == "executing"
    env.clock[0] += timedelta(seconds=2)
    await worker.sweeper.cycle()
    s = await state(env)
    assert (s["incident"]["status"], s["incident"]["status_reason"]) == (
        "escalated",
        "runner_timeout",
    )
    assert (s["executions"][0]["status"], s["executions"][0]["error_code"]) == (
        "failed",
        "RUNNER_TIMEOUT",
    )
    # A late result changes nothing, and no rollback is proposed.
    await worker.deliver(result(env, execution_id, "succeeded", health=True))
    assert await state(env) == s
    await detail_validates(env)


async def test_sweeper_expires_pending_proposals(env: Env, worker: Worker) -> None:
    early = (await env.challenge()).json()  # taken while the proposal was still pending
    env.clock[0] += timedelta(seconds=899)
    await worker.sweeper.cycle()
    assert (await state(env))["proposals"][0]["status"] == "pending"
    env.clock[0] += timedelta(seconds=2)
    await worker.sweeper.cycle()
    s = await state(env)
    assert s["proposals"][0]["status"] == "expired"
    assert (s["incident"]["status"], s["incident"]["status_reason"]) == (
        "escalated",
        "proposal_expired",
    )
    # FR-013: a later approve (and a new challenge) says PROPOSAL_EXPIRED.
    body = {
        "challenge_id": early["challenge_id"],
        "nonce": early["nonce"],
        "proposal_fingerprint": FINGERPRINT,
        "auth_method": "biometric",
    }
    late = await env.approve(body=body, key=str(uuid.uuid4()))
    assert (late.status_code, late.json()["error"]["code"]) == (409, "PROPOSAL_EXPIRED")
    again = await env.challenge()
    assert (again.status_code, again.json()["error"]["code"]) == (409, "PROPOSAL_EXPIRED")
    await detail_validates(env)
