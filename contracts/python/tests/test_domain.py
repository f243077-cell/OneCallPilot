import copy
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError
from samples import (
    approval,
    deploy_evidence,
    execution,
    hypothesis,
    incident_detail,
    incident_summary,
    log_evidence,
    metric_evidence,
    proposal,
    runbook_evidence,
    seed_evidence,
    uid,
)

from oncallpilot_contracts import domain, enums


def rejects(model: type[BaseModel], data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


# --- the samples are valid -------------------------------------------------


def test_samples_validate() -> None:
    domain.IncidentDetail.model_validate(incident_detail())
    domain.IncidentSummary.model_validate(incident_summary())
    domain.Execution.model_validate(execution())
    domain.Approval.model_validate(approval())


def test_json_round_trip_keeps_values_and_z_suffix() -> None:
    detail = domain.IncidentDetail.model_validate(incident_detail())
    dumped = detail.model_dump(mode="json")
    assert dumped["opened_at"] == "2026-10-14T09:00:25Z"
    assert domain.IncidentDetail.model_validate(dumped) == detail


def test_models_are_frozen_and_strict() -> None:
    summary = domain.IncidentSummary.model_validate(incident_summary())
    with pytest.raises(ValidationError):
        summary.status = "resolved"  # type: ignore[misc]
    rejects(domain.IncidentSummary, {**incident_summary(), "extra_field": 1})


# --- value sets match architecture.md ---------------------------------------


@pytest.mark.parametrize(
    ("literal", "size"),
    [
        (enums.IncidentStatus, 7),
        (enums.EscalationReason, 13),
        (enums.RootCauseCategory, 13),
        (enums.MetricTemplate, 12),
        (enums.ToolName, 5),
        (enums.EvidenceKind, 6),
        (enums.ProposalStatus, 8),
        (enums.AuditEvent, 21),
        (enums.ErrorCode, 17),
    ],
)
def test_value_set_sizes(literal: Any, size: int) -> None:
    values = get_args(literal)
    assert len(values) == size
    assert len(set(values)) == size


# --- timestamps -------------------------------------------------------------


@pytest.mark.parametrize("opened_at", ["2026-10-14T09:00:25", "2026-10-14T11:00:25+02:00"])
def test_timestamps_must_be_utc(opened_at: str) -> None:
    rejects(domain.IncidentSummary, {**incident_summary(), "opened_at": opened_at})


# --- evidence ---------------------------------------------------------------


@pytest.mark.parametrize(
    "build", [seed_evidence, log_evidence, metric_evidence, deploy_evidence, runbook_evidence]
)
def test_payload_must_match_kind(build: Any) -> None:
    item = build()
    other = "metric_query" if item["kind"] != "metric_query" else "log_query"
    tool = {"metric_query": "query_metrics", "log_query": "query_logs"}[other]
    rejects(domain.Evidence, {**item, "kind": other, "tool_name": tool})


def test_seed_evidence_has_no_tool() -> None:
    rejects(domain.Evidence, {**seed_evidence(), "tool_name": "query_logs"})
    rejects(domain.Evidence, {**log_evidence(), "purpose": "seed"})


def test_tool_must_match_kind() -> None:
    rejects(domain.Evidence, {**log_evidence(), "tool_name": "query_metrics"})
    rejects(domain.Evidence, {**log_evidence(), "tool_name": None})


@pytest.mark.parametrize("ref", ["E", "E1234", "e2", "X2", "E-1"])
def test_evidence_ref_pattern(ref: str) -> None:
    rejects(domain.Evidence, {**log_evidence(), "ref": ref})


def test_log_payload_caps() -> None:
    item = log_evidence()
    line = item["payload"]["lines"][0]
    item["payload"]["lines"] = [line] * 51
    rejects(domain.Evidence, item)
    item = log_evidence()
    item["payload"]["lines"][0]["msg"] = "x" * 201
    rejects(domain.Evidence, item)


def test_metric_payload_caps() -> None:
    item = metric_evidence()
    point = item["payload"]["points"][0]
    item["payload"]["points"] = [point] * 121
    rejects(domain.Evidence, item)
    item = metric_evidence()
    item["payload"]["template"] = "disk_usage"
    rejects(domain.Evidence, item)


def test_deploy_list_is_newest_first() -> None:
    item = deploy_evidence()
    item["payload"]["records"].reverse()
    rejects(domain.Evidence, item)


# --- hypotheses -------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"evidence_refs": []},
        {"confidence": 1.01},
        {"confidence": -0.01},
        {"rank": 4},
        {"root_cause_category": "cosmic_rays"},
        {"summary": "too short"},
    ],
)
def test_invalid_hypothesis(overrides: dict[str, Any]) -> None:
    rejects(domain.Hypothesis, {**hypothesis(), **overrides})


# --- proposals --------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "rollback"},  # rollback needs a parent
        {"parent_proposal_id": uid(99)},  # primary must not have one
        {"approval_requirement": "tap"},  # medium tier needs biometric
        {"risk_tier": "low"},  # low tier needs tap
        {"action": "run_migration_rollback"},  # disabled in v1
        {"action": "exec_shell"},
        {"params": {"service": "api", "target_release": True}},
        {"params": {"Service": "api"}},
        {"params": {"service": ["api"]}},
        {"evidence_refs": []},  # a primary proposal cites evidence
        {"expires_at": "2026-10-14T09:01:05Z"},
        {"fingerprint": "not-a-hash"},
    ],
)
def test_invalid_proposal(overrides: dict[str, Any]) -> None:
    rejects(domain.Proposal, {**proposal(), **overrides})


def test_rollback_proposal_needs_parent() -> None:
    rollback = {**proposal(), "id": uid(31), "kind": "rollback", "parent_proposal_id": uid(30)}
    domain.Proposal.model_validate({**rollback, "evidence_refs": []})


def test_low_tier_uses_tap() -> None:
    scale = {
        **proposal(),
        "action": "scale_service",
        "params": {"service": "api", "replicas": 3},
        "risk_tier": "low",
        "approval_requirement": "tap",
    }
    domain.Proposal.model_validate(scale)


# --- executions and health ---------------------------------------------------


def test_execution_times_follow_status() -> None:
    rejects(domain.Execution, {**execution(), "finished_at": None})
    rejects(
        domain.Execution,
        {**execution(), "status": "queued", "finished_at": None},
    )
    domain.Execution.model_validate(
        {
            **execution(),
            "status": "queued",
            "started_at": None,
            "finished_at": None,
            "health_after": None,
        }
    )


def test_health_verdict_matches_checks() -> None:
    data = execution()
    data["health_after"]["structural"]["passed"] = False
    rejects(domain.Execution, data)
    data["health_after"]["verdict"] = "fail"
    domain.Execution.model_validate(data)


def test_runner_timeout_is_an_execution_error_code() -> None:
    data = {**execution(), "status": "failed", "error_code": "RUNNER_TIMEOUT", "health_after": None}
    domain.Execution.model_validate(data)
    rejects(domain.Execution, {**data, "error_code": "SOMETHING_ELSE"})


# --- incidents --------------------------------------------------------------


def test_resolved_needs_time_and_resolution() -> None:
    rejects(domain.IncidentSummary, {**incident_summary(), "status": "resolved"})
    domain.IncidentSummary.model_validate(
        {
            **incident_summary(),
            "status": "resolved",
            "resolved_at": "2026-10-14T09:04:00Z",
            "resolution": "action_succeeded",
            "pending_proposal_id": None,
        }
    )


def test_escalated_needs_reason() -> None:
    rejects(domain.IncidentSummary, {**incident_summary(), "status": "escalated"})
    rejects(
        domain.IncidentSummary,
        {**incident_summary(), "status": "escalated", "status_reason": "felt_like_it"},
    )


def test_detail_rejects_unknown_citation() -> None:
    data = incident_detail()
    data["hypotheses"][0]["evidence_refs"] = ["E2", "E9"]
    rejects(domain.IncidentDetail, data)


def test_detail_rejects_duplicate_refs() -> None:
    data = incident_detail()
    data["evidence"][2]["ref"] = "E2"
    rejects(domain.IncidentDetail, data)


def test_detail_rejects_two_pending_proposals() -> None:
    data = incident_detail()
    second = copy.deepcopy(data["proposals"][0])
    second["id"] = uid(32)
    data["proposals"].append(second)
    rejects(domain.IncidentDetail, data)


def test_detail_pending_proposal_id_must_match() -> None:
    rejects(domain.IncidentDetail, {**incident_detail(), "pending_proposal_id": None})


def test_detail_window_end_only_when_resolved() -> None:
    rejects(domain.IncidentDetail, {**incident_detail(), "window_end": "2026-10-14T09:05:00Z"})


def test_detail_rejects_duplicate_active_rank() -> None:
    data = incident_detail()
    data["hypotheses"].append({**hypothesis(1), "id": uid(29)})
    rejects(domain.IncidentDetail, data)


# --- approvals --------------------------------------------------------------


def test_approval_rules() -> None:
    rejects(domain.Approval, {**approval(), "challenge_id": None})
    rejection = {**approval(), "decision": "rejected", "challenge_id": None, "auth_method": "tap"}
    rejects(domain.Approval, {**rejection, "reason": "no"})
    domain.Approval.model_validate({**rejection, "reason": "wrong service"})


# --- review fixes (contract-review-a.md issues 2–5) ----------------------------


def test_metric_unit_follows_template() -> None:
    item = metric_evidence()
    item["payload"]["unit"] = "seconds"
    rejects(domain.Evidence, item)
    assert set(domain.METRIC_UNITS) == set(get_args(enums.MetricTemplate))


@pytest.mark.parametrize("service", ["redis", "payments", "lb", "postgres"])
def test_metrics_only_for_api_and_worker(service: str) -> None:
    item = metric_evidence()
    item["payload"]["service"] = service
    rejects(domain.Evidence, item)


@pytest.mark.parametrize(
    "overrides", [{"replicas": 0}, {"replicas": 6}, {"reason": ""}, {"image_tag": "x"}]
)
def test_deploy_list_item_carries_replicas_and_reason(overrides: dict[str, Any]) -> None:
    item = deploy_evidence()
    item["payload"]["records"][0].update(overrides)
    rejects(domain.Evidence, item)
    item = deploy_evidence()
    del item["payload"]["records"][0]["reason"]
    rejects(domain.Evidence, item)


def monitor_settings() -> dict[str, Any]:
    return {
        "version": 3,
        "services": ["api", "worker"],
        "thresholds": {"error_rate.z": 4.0},
        "notify": {"min_push_severity": "sev2"},
        "locked": True,
        "lock_reason": "Benchmark lock is on",
        "updated_by": None,
        "updated_at": "2026-10-14T08:00:00Z",
    }


def test_lock_reason_exactly_when_locked() -> None:
    domain.MonitorSettings.model_validate(monitor_settings())
    domain.MonitorSettings.model_validate(
        {**monitor_settings(), "locked": False, "lock_reason": None}
    )
    rejects(domain.MonitorSettings, {**monitor_settings(), "lock_reason": None})
    rejects(domain.MonitorSettings, {**monitor_settings(), "locked": False})
