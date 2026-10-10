"""Enumerations shared by the contracts (C1; architecture §5.7, §6.3, §6.5, §7.9, §8.2, §9.8).

Every value set below is copied from ``docs/architecture.md``. Adding a value
is a contract change (``CLAUDE.md`` §8.4). Clients map values they do not
know to an ``unknown`` case instead of failing (MOB-020).
"""

from typing import Literal

# --- Incidents (§8.2) -------------------------------------------------------

IncidentStatus = Literal[
    "investigating",
    "awaiting_approval",
    "escalated",
    "executing",
    "verifying",
    "resolved",
    "action_failed",
]
Severity = Literal["sev1", "sev2", "sev3"]
Resolution = Literal["action_succeeded", "auto_recovered", "manual", "rolled_back"]

# Logical service names, equal to the ``oncallpilot.service`` label (C7 §1).
ServiceName = Literal["api", "worker", "lb", "payments", "redis", "postgres"]

# ``incidents.status_reason`` when ``status = escalated`` (§5.7).
EscalationReason = Literal[
    "low_confidence",
    "no_catalogue_action_fits",
    "budget_exhausted",
    "llm_unavailable",
    "llm_output_invalid",
    "proposal_invalid",
    "dry_run_unavailable",
    "proposal_rejected",
    "proposal_expired",
    "rolled_back_after_failed_action",
    "runner_refused",
    "runner_timeout",
    "agent_interrupted",
]

# --- Detector signals and alerts (§6.8, §9.2) --------------------------------

SignalName = Literal["error_rate", "p95_latency", "job_failure_rate", "restarts", "stack_traces"]

# Who sent an alert to ``POST /ingest/alert``: the built-in detector, or
# Alertmanager through the optional adapter (FR-008).
AlertSource = Literal["detector", "alertmanager"]

# --- Evidence (§6.3, §8.2) --------------------------------------------------

EvidenceKind = Literal[
    "detector_signal",
    "log_query",
    "metric_query",
    "deploy_list",
    "service_health",
    "runbook_hit",
]
EvidencePurpose = Literal["seed", "gather", "disproof"]

# The read-only tools that produce evidence (§6.3). ``propose_action`` is not one.
ToolName = Literal[
    "query_logs",
    "query_metrics",
    "get_recent_deploys",
    "get_service_health",
    "search_runbooks",
]

# The 12 fixed PromQL templates of ``query_metrics`` (§6.3).
MetricTemplate = Literal[
    "error_rate",
    "p95_latency",
    "request_rate",
    "memory_rss",
    "cpu_usage",
    "process_restarts",
    "db_pool_in_use",
    "db_pool_wait_p95",
    "cache_errors",
    "upstream_latency_p95",
    "job_failure_rate",
    "job_latency_p95",
]

# Log levels as stored in evidence: the C7 levels, plus UNKNOWN for lines that
# are not JSON (C7 §3.3).
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "UNKNOWN"]

# --- Hypotheses (§6.5, §8.2) ------------------------------------------------

RootCauseCategory = Literal[
    "bad_deploy",
    "config_error",
    "memory_leak",
    "resource_saturation",
    "traffic_surge",
    "dependency_unavailable",
    "dependency_slow",
    "db_connection_exhaustion",
    "cache_failure",
    "application_bug",
    "network_issue",
    "disk_pressure",
    "unknown",
]
HypothesisStatus = Literal["active", "dropped"]

# --- Proposals, approvals, executions (§7.2, §8.2) -----------------------------

# Actions a proposal may name: the ``enabled: true`` actions of
# ``contracts/actions.yaml`` (C4). A test keeps the two in step.
EnabledAction = Literal["restart_service", "scale_service", "rollback_deploy", "clear_cache"]

RiskTier = Literal["low", "medium", "high"]
ApprovalRequirement = Literal["tap", "biometric"]
AuthMethod = Literal["biometric", "tap"]
ProposalKind = Literal["primary", "rollback"]
ProposalStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "expired",
    "superseded",
    "executing",
    "succeeded",
    "failed",
]
Decision = Literal["approved", "rejected"]
ExecutionStatus = Literal["queued", "running", "succeeded", "failed", "aborted"]

# Status of one runner step (``execution.progress``, RUN-013).
StepStatus = Literal["running", "succeeded", "failed"]

# Docker container state and health, as reported by the Docker API.
DockerState = Literal["created", "restarting", "running", "removing", "paused", "exited", "dead"]
DockerHealth = Literal["healthy", "unhealthy", "starting", "none"]

# Error codes a runner result may carry (RUN-001…RUN-008, RUN-018). The worker
# adds RUNNER_TIMEOUT when no final result arrives in time (FR-024).
RunnerErrorCode = Literal[
    "BAD_SIGNATURE",
    "REQUEST_EXPIRED",
    "ACTION_NOT_ALLOWED",
    "TARGET_NOT_ALLOWED",
    "VALIDATION_ERROR",
    "RATE_LIMITED",
    "COOLDOWN_ACTIVE",
    "STATE_DRIFT",
]
ExecutionErrorCode = Literal[
    "BAD_SIGNATURE",
    "REQUEST_EXPIRED",
    "ACTION_NOT_ALLOWED",
    "TARGET_NOT_ALLOWED",
    "VALIDATION_ERROR",
    "RATE_LIMITED",
    "COOLDOWN_ACTIVE",
    "STATE_DRIFT",
    "RUNNER_TIMEOUT",
]

# --- Audit (§7.9) -----------------------------------------------------------

AuditEvent = Literal[
    "incident.opened",
    "incident.status_changed",
    "evidence.added",
    "hypothesis.created",
    "hypothesis.reflected",
    "llm.call",
    "agent.interrupted",
    "proposal.created",
    "proposal.rejected_by_validator",
    "proposal.expired",
    "proposal.superseded",
    "challenge.issued",
    "approval.approved",
    "approval.rejected",
    "execution.queued",
    "execution.started",
    "execution.finished",
    "health.verified",
    "incident.resolved",
    "settings.updated",
    "device.registered",
]

# --- API errors (§9.8) ------------------------------------------------------

ErrorCode = Literal[
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "VALIDATION_ERROR",
    "IDEMPOTENCY_KEY_REQUIRED",
    "IDEMPOTENCY_CONFLICT",
    "CHALLENGE_INVALID",
    "STALE_PROPOSAL",
    "PROPOSAL_NOT_PENDING",
    "PROPOSAL_EXPIRED",
    "BIOMETRIC_REQUIRED",
    "RATE_LIMITED",
    "COOLDOWN_ACTIVE",
    "INCIDENT_BUSY",
    "VERSION_CONFLICT",
    "BENCHMARK_LOCKED",
    "INTERNAL",
]
