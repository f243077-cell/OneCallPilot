"""Generate ``contracts/schemas/*.json`` from the Pydantic models.

Run ``uv run python -m oncallpilot_contracts.schema_export`` from
``contracts/python`` after changing a model. CI regenerates the schemas and
fails if they differ from the committed files (TEST-009 item 1). Never edit
the JSON files by hand.
"""

import json
from pathlib import Path

from pydantic import BaseModel

from oncallpilot_contracts import api, approval, domain, push, runner, ws
from oncallpilot_contracts.ledger import DeployRecord

# Schema file name (without .json) -> model. Add new contract models here.
SCHEMAS: dict[str, type[BaseModel]] = {
    "deploy_record": DeployRecord,
    # C1 domain models
    "incident_summary": domain.IncidentSummary,
    "incident_detail": domain.IncidentDetail,
    "evidence": domain.Evidence,
    "evidence_payload_detector_signal": domain.DetectorSignalPayload,
    "evidence_payload_log_query": domain.LogQueryPayload,
    "evidence_payload_metric_query": domain.MetricQueryPayload,
    "evidence_payload_deploy_list": domain.DeployListPayload,
    "evidence_payload_service_health": domain.ServiceHealthPayload,
    "evidence_payload_runbook_hit": domain.RunbookHitPayload,
    "hypothesis": domain.Hypothesis,
    "proposal": domain.Proposal,
    "dry_run_result": domain.DryRunResult,
    "execution": domain.Execution,
    "approval": domain.Approval,
    "audit_entry": domain.AuditEntry,
    "device": domain.Device,
    "monitor_settings": domain.MonitorSettings,
    # C5 runner messaging
    "runner_request": runner.RunnerRequest,
    "runner_result": runner.RunnerResult,
    # C8 approval protocol
    "challenge_request": approval.ChallengeRequest,
    "challenge_response": approval.ChallengeResponse,
    "approve_request": approval.ApproveRequest,
    "approve_accepted": approval.ApproveAccepted,
    "reject_request": approval.RejectRequest,
    "reject_response": approval.RejectResponse,
    # C9 push payload
    "push_payload": push.PushPayload,
    # C3 WebSocket protocol
    "ws_client_message": ws.WsClientMessage,
    "ws_server_message": ws.WsServerMessage,
    # C2 REST bodies (openapi.yaml refers to these files)
    "healthz": api.Healthz,
    "alert_in": api.AlertIn,
    "ingest_accepted": api.IngestAccepted,
    "incident_page": api.IncidentPage,
    "log_tail": api.LogTail,
    "resolve_request": api.ResolveRequest,
    "device_register": api.DeviceRegister,
    "device_registered": api.DeviceRegistered,
    "audit_page": api.AuditPage,
    "monitor_settings_update": api.MonitorSettingsUpdate,
    "error_envelope": api.ErrorEnvelope,
}

SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def render_schema(model: type[BaseModel]) -> str:
    return (
        json.dumps(model.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def main() -> None:
    for name, model in SCHEMAS.items():
        path = SCHEMAS_DIR / f"{name}.json"
        path.write_text(render_schema(model), encoding="utf-8", newline="\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
