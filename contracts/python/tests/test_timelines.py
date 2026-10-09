"""Phase 0 timelines: valid, internally consistent, and faithful to the catalogue and FR-009."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from oncallpilot_contracts.signing import canonical_json
from oncallpilot_contracts.timeline import Timeline

CONTRACTS_DIR = Path(__file__).resolve().parents[2]
TIMELINES_DIR = CONTRACTS_DIR / "fixtures" / "timelines"
CATALOGUE: dict[str, Any] = yaml.safe_load((CONTRACTS_DIR / "actions.yaml").read_text("utf-8"))
NAMES = ["bad_deploy_success", "slow_dependency_escalated", "scale_failed_rollback"]

# FR-009: the only allowed incident status transitions.
TRANSITIONS = {
    "investigating": {"awaiting_approval", "escalated", "resolved"},
    "awaiting_approval": {"executing", "escalated", "resolved"},
    "executing": {"verifying", "action_failed", "awaiting_approval", "escalated"},
    "verifying": {"resolved", "action_failed", "escalated"},
    "action_failed": {"awaiting_approval", "escalated", "resolved"},
    "escalated": {"resolved"},
}


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((TIMELINES_DIR / f"{name}.json").read_text("utf-8"))
    return data


def events(doc: dict[str, Any], name: str | None = None) -> list[dict[str, Any]]:
    found = [step["event"] for step in doc["steps"]]
    return [event for event in found if name is None or event["event"] == name]


def test_exactly_the_three_phase0_timelines() -> None:
    assert sorted(path.stem for path in TIMELINES_DIR.glob("*.json")) == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_timeline_validates(name: str) -> None:
    doc = load(name)
    assert doc["name"] == name
    Timeline.model_validate(doc)


@pytest.mark.parametrize("name", NAMES)
def test_evidence_matches_events(name: str) -> None:
    doc = load(name)
    added = [e["data"] for e in events(doc, "evidence.added")]
    final = doc["final"]["evidence"]
    refs = [item["ref"] for item in final]
    assert refs == [f"E{i}" for i in range(1, len(refs) + 1)]  # gap-free (AI-010)
    assert final[0]["purpose"] == "seed"  # E1 arrives with the incident, not as an event
    assert [{k: v for k, v in d.items() if k != "payload_truncated"} for d in added] == final[1:]


@pytest.mark.parametrize("name", NAMES)
def test_final_hypotheses_are_the_last_set(name: str) -> None:
    doc = load(name)
    assert events(doc, "hypothesis.updated")[-1]["data"]["hypotheses"] == doc["final"]["hypotheses"]
    top = next(h for h in doc["final"]["hypotheses"] if h["rank"] == 1 and h["status"] == "active")
    assert doc["final"]["top_hypothesis"] == {
        "summary": top["summary"],
        "confidence": top["confidence"],
        "root_cause_category": top["root_cause_category"],
    }


@pytest.mark.parametrize("name", NAMES)
def test_proposals_match_events_and_catalogue(name: str) -> None:
    doc = load(name)
    final = {p["id"]: p for p in doc["final"]["proposals"]}
    created = [e["data"] for e in events(doc, "proposal.created")]
    assert [p["id"] for p in created] == list(final)
    last_status = {p["id"]: "pending" for p in created}
    for update in events(doc, "proposal.updated"):
        last_status[update["data"]["proposal_id"]] = update["data"]["status"]
    for proposal in created:
        assert final[proposal["id"]] == {**proposal, "status": last_status[proposal["id"]]}
        entry = CATALOGUE["actions"][proposal["action"]]
        assert entry["enabled"] is True
        assert proposal["risk_tier"] == entry["risk_tier"]
        assert proposal["approval_requirement"] == entry["approval_requirement"]
        assert set(proposal["params"]) == set(entry["params"])
        for param, value in proposal["params"].items():
            spec = entry["params"][param]
            if "values" in spec:
                assert value in spec["values"], (param, value)
            if spec["type"] == "integer":
                assert spec["minimum"] <= value <= spec["maximum"]
        if proposal["kind"] == "primary":
            expected_action = entry["rollback_step"]["action"] if entry["rollback_step"] else None
            step = proposal["rollback_step"]
            assert (step["action"] if step else None) == expected_action


@pytest.mark.parametrize("name", NAMES)
def test_proposal_fingerprints_follow_c8(name: str) -> None:
    for proposal in load(name)["final"]["proposals"]:
        body = {
            "proposal_id": proposal["id"],
            "action": proposal["action"],
            "params": proposal["params"],
            "risk_tier": proposal["risk_tier"],
            "state_fingerprint": proposal["dry_run_result"]["state_fingerprint"],
            "dry_run_at": proposal["dry_run_at"],
        }
        assert proposal["fingerprint"] == hashlib.sha256(canonical_json(body)).hexdigest()


@pytest.mark.parametrize("name", NAMES)
def test_status_transitions_follow_fr009(name: str) -> None:
    status = "investigating"
    for event in events(load(name)):
        if event["event"] == "incident.updated":
            new = event["data"]["status"]
        elif event["event"] == "incident.resolved":
            new = "resolved"
        else:
            continue
        assert new in TRANSITIONS[status], f"{status} -> {new}"
        status = new


@pytest.mark.parametrize("name", NAMES)
def test_approval_pauses_are_on_pending_proposals(name: str) -> None:
    doc = load(name)
    pending: set[str] = set()
    for step in doc["steps"]:
        event = step["event"]
        if event["event"] == "proposal.created":
            pending.add(event["data"]["id"])
        if step.get("await_approval"):
            assert event["event"] == "proposal.updated"
            assert event["data"]["status"] == "approved"
            assert event["data"]["proposal_id"] in pending


@pytest.mark.parametrize("name", NAMES)
def test_execution_steps_match_progress_events(name: str) -> None:
    doc = load(name)
    for execution in doc["final"]["executions"]:
        progress = [
            (e["ts"], e["data"]["step"], e["data"]["status"], e["data"]["message"])
            for e in events(doc, "execution.progress")
            if e["data"]["execution_id"] == execution["id"]
        ]
        steps = [(s["at"], s["step"], s["status"], s["message"]) for s in execution["steps"]]
        assert steps == progress


@pytest.mark.parametrize("name", NAMES)
def test_budgets(name: str) -> None:
    final = load(name)["final"]
    tools = [e for e in final["evidence"] if e["purpose"] != "seed"]
    gather = [e for e in tools if e["purpose"] == "gather"]
    disproof = [e for e in tools if e["purpose"] == "disproof"]
    proposed = any(p["kind"] == "primary" for p in final["proposals"])
    assert len(gather) <= 6 and len(disproof) == 1  # AI-002, AI-015
    assert final["agent_meta"]["tool_calls"] == len(tools) + int(proposed) <= 8
    assert final["agent_meta"]["llm_calls"] <= 15  # AI-029


def test_bad_deploy_success_ends_resolved() -> None:
    doc = load("bad_deploy_success")
    final = doc["final"]
    assert (final["status"], final["resolution"]) == ("resolved", "action_succeeded")
    assert [p["action"] for p in final["proposals"]] == ["rollback_deploy"]
    assert final["proposals"][0]["approval_requirement"] == "biometric"
    assert final["executions"][0]["health_after"]["verdict"] == "pass"
    resolved = events(doc, "incident.resolved")[-1]["data"]
    assert (resolved["resolved_at"], resolved["resolution"]) == (
        final["resolved_at"],
        final["resolution"],
    )


def test_slow_dependency_escalates_without_a_proposal() -> None:
    final = load("slow_dependency_escalated")["final"]
    assert (final["status"], final["status_reason"]) == ("escalated", "no_catalogue_action_fits")
    assert final["proposals"] == [] and final["executions"] == []
    assert final["agent_meta"]["terminal_state"] == "ESCALATED"


def test_scale_failed_rollback_offers_the_captured_count() -> None:
    doc = load("scale_failed_rollback")
    final = doc["final"]
    assert (final["status"], final["resolution"]) == ("resolved", "rolled_back")
    primary, rollback = final["proposals"]
    assert rollback["kind"] == "rollback" and rollback["parent_proposal_id"] == primary["id"]
    captured = primary["dry_run_result"]["current"]["running_replicas"]
    assert rollback["params"] == {"service": "api", "replicas": captured}  # FR-015
    assert primary["rollback_step"]["params"] == rollback["params"]
    first, second = final["executions"]
    assert first["health_after"]["verdict"] == "fail"
    assert second["health_after"]["verdict"] == "pass"
    statuses = [e["data"]["status"] for e in events(doc, "incident.updated")]
    assert "action_failed" in statuses


def test_broken_timeline_is_rejected() -> None:
    doc = load("bad_deploy_success")
    doc["steps"][3], doc["steps"][4] = doc["steps"][4], doc["steps"][3]
    with pytest.raises(ValidationError):
        Timeline.model_validate(doc)
