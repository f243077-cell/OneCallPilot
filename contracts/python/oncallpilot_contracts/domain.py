"""C1 — Domain models (architecture §6.3, §6.7, §7.5, §8.2, §9.3; DB-002…DB-010).

These are the resource shapes that backend-api returns over REST (C2) and
over the WebSocket (C3), that the app's Dart DTOs mirror (MOB-020), and that
the fixture timelines use. Names and value sets come from
``docs/architecture.md``; the enumerations are in ``enums.py``.

Conventions
    Every key is always present. A value that does not apply is ``null``,
    never a missing key. Identifiers are UUIDs. The model and the user see
    evidence only by its ``ref`` (``E1``, ``E2``, …), never by UUID (§6.4).

Evidence payloads
    ``Evidence.payload`` holds **redacted** content only (§7.8) and has one
    shape per ``kind``: ``detector_signal`` → ``DetectorSignalPayload``,
    ``log_query`` → ``LogQueryPayload``, ``metric_query`` →
    ``MetricQueryPayload``, ``deploy_list`` → ``DeployListPayload``,
    ``service_health`` → ``ServiceHealthPayload``, ``runbook_hit`` →
    ``RunbookHitPayload``. Each has its own ``evidence_payload_<kind>.json``
    schema, and ``Evidence`` validates the payload against it.

Invariants checked here (they mirror the database checks of §8.2)
    - an incident is ``resolved`` exactly when ``resolved_at`` and
      ``resolution`` are set, and ``window_end`` is set exactly then (FR-007);
    - an ``escalated`` incident has a ``status_reason`` (FR-010);
    - a rollback proposal has a parent, a primary proposal has none (DB-005);
    - ``approval_requirement`` follows the tier: low → tap, otherwise
      biometric (C4);
    - a rejected decision has a reason, an approval has a challenge (DB-006);
    - inside an ``IncidentDetail``, every cited ``E#`` exists, refs are
      unique, there is at most one pending proposal, and active hypothesis
      ranks are unique (DB-003…DB-005).
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    Field,
    JsonValue,
    StrictInt,
    StrictStr,
    StringConstraints,
    ValidationError,
    model_validator,
)

from oncallpilot_contracts.common import (
    ContainerName,
    ContractModel,
    EvidenceRef,
    Semver,
    Sha256Hex,
    UtcDatetime,
)
from oncallpilot_contracts.enums import (
    AlertSource,
    ApprovalRequirement,
    AuditEvent,
    AuthMethod,
    Decision,
    DockerHealth,
    DockerState,
    EnabledAction,
    EscalationReason,
    EvidenceKind,
    EvidencePurpose,
    ExecutionErrorCode,
    ExecutionStatus,
    HypothesisStatus,
    IncidentStatus,
    LogLevel,
    MetricTemplate,
    ProposalKind,
    ProposalStatus,
    Resolution,
    RiskTier,
    RootCauseCategory,
    ServiceName,
    Severity,
    SignalName,
    StepStatus,
    ToolName,
)
from oncallpilot_contracts.ledger import DeployedBy, DeployKind, LedgerService

ParamName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ParamValue = StrictStr | StrictInt
ActionParams = dict[ParamName, ParamValue]
"""Action parameters: names and values are checked against ``contracts/actions.yaml`` (C4)."""

ReleaseLabel = Annotated[str, StringConstraints(pattern=r"^[0-9]+(\.[0-9]+){1,2}$")]
"""Value of ``oncallpilot.release``: a release such as ``1.4.0``, or an image version such as
``17.11`` for third-party images (C7 §1)."""

FINAL_EXECUTION_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "aborted"})


# --- Alerts and triggers -----------------------------------------------------


class Signal(ContractModel):
    """One detector signal (§6.8), as sent in ``AlertIn`` (§9.2)."""

    name: SignalName
    value: float
    baseline: float | None
    zscore: float | None
    first_anomalous_at: UtcDatetime


class TriggerUpdate(ContractModel):
    """A later alert for an incident that was still open (deduplicated, FR-005)."""

    signals: list[Signal] = Field(min_length=1)
    observed_at: UtcDatetime


class Trigger(ContractModel):
    """``incidents.trigger``: the alert that opened the incident, plus later updates."""

    source: AlertSource
    signals: list[Signal] = Field(min_length=1)
    observed_at: UtcDatetime
    updates: list[TriggerUpdate]


# --- Evidence payloads (§6.3) ------------------------------------------------


class DetectorSignalPayload(ContractModel):
    """``kind = detector_signal``: the signal snapshot of the alert (seed evidence)."""

    service: ServiceName
    signals: list[Signal] = Field(min_length=1)
    observed_at: UtcDatetime


class LogLine(ContractModel):
    """One redacted log line (``query_logs`` evidence and ``GET /incidents/{id}/log-tail``)."""

    ts: UtcDatetime
    level: LogLevel
    msg: str = Field(max_length=200)
    exc_type: str | None = Field(max_length=100)


class ExceptionCount(ContractModel):
    exc_type: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=1)


class LogQueryPayload(ContractModel):
    """``kind = log_query``: result of ``query_logs`` (AI-005)."""

    lines: list[LogLine] = Field(max_length=50)
    total_count: int = Field(ge=0, description="Matching lines in the window, before the cap")
    top_exception_types: list[ExceptionCount] = Field(max_length=5)


class MetricPoint(ContractModel):
    ts: UtcDatetime
    value: float


class MetricQueryPayload(ContractModel):
    """``kind = metric_query``: result of ``query_metrics`` (AI-006)."""

    template: MetricTemplate
    service: ServiceName
    step_seconds: int = Field(ge=5)
    points: list[MetricPoint] = Field(max_length=120)
    baseline_mean: float | None
    peak: float | None
    pct_change: float | None
    change_point_at: UtcDatetime | None
    note: str | None = Field(max_length=200, description="For example, why the series is empty")


class DeployListItem(ContractModel):
    """One deploy-ledger record as ``get_recent_deploys`` returns it (C6 fields)."""

    service: LedgerService
    release: Semver
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    commit_message: str = Field(min_length=1, max_length=200)
    config_hash: Sha256Hex
    deployed_at: UtcDatetime
    deployed_by: DeployedBy
    kind: DeployKind


class DeployListPayload(ContractModel):
    """``kind = deploy_list``: result of ``get_recent_deploys``, newest first (AI-007)."""

    records: list[DeployListItem] = Field(max_length=10)

    @model_validator(mode="after")
    def _newest_first(self) -> Self:
        times = [record.deployed_at for record in self.records]
        if times != sorted(times, reverse=True):
            raise ValueError("records must be ordered newest first")
        return self


class ContainerState(ContractModel):
    """State of one container (dry runs and structural health checks)."""

    name: ContainerName
    state: DockerState
    health: DockerHealth


class ContainerHealth(ContractModel):
    """One container in ``get_service_health``: projected fields only (AI-008)."""

    name: ContainerName
    release: ReleaseLabel
    state: DockerState
    health: DockerHealth
    restart_count: int = Field(ge=0)
    oom_killed: bool
    exit_code: int | None
    started_at: UtcDatetime | None


class ProbeResult(ContractModel):
    """An HTTP ``/healthz`` probe."""

    url: str = Field(min_length=1, max_length=200)
    ok: bool
    status_code: int | None = Field(ge=100, le=599)
    latency_ms: float | None = Field(ge=0)
    error: str | None = Field(max_length=200)


class ServiceHealthPayload(ContractModel):
    """``kind = service_health``: result of ``get_service_health`` (AI-008).

    Never holds container environment variables, mounts, or labels outside
    ``oncallpilot.*``.
    """

    service: ServiceName
    containers: list[ContainerHealth]
    probe: ProbeResult | None


class RunbookChunkHit(ContractModel):
    source: str = Field(pattern=r"^runbooks/[A-Za-z0-9_.-]+\.md$")
    heading: str | None = Field(max_length=200)
    content: str = Field(min_length=1, max_length=1500)
    similarity: float = Field(ge=-1, le=1)


class RunbookHitPayload(ContractModel):
    """``kind = runbook_hit``: result of ``search_runbooks`` (AI-009)."""

    chunks: list[RunbookChunkHit] = Field(max_length=3)


EVIDENCE_PAYLOADS: dict[EvidenceKind, type[ContractModel]] = {
    "detector_signal": DetectorSignalPayload,
    "log_query": LogQueryPayload,
    "metric_query": MetricQueryPayload,
    "deploy_list": DeployListPayload,
    "service_health": ServiceHealthPayload,
    "runbook_hit": RunbookHitPayload,
}

TOOL_EVIDENCE_KIND: dict[ToolName, EvidenceKind] = {
    "query_logs": "log_query",
    "query_metrics": "metric_query",
    "get_recent_deploys": "deploy_list",
    "get_service_health": "service_health",
    "search_runbooks": "runbook_hit",
}


# --- Evidence and hypotheses ------------------------------------------------


class Evidence(ContractModel):
    """One evidence item (§5.3, §9.3; DB-003)."""

    id: UUID
    ref: EvidenceRef
    kind: EvidenceKind
    purpose: EvidencePurpose
    tool_name: ToolName | None = Field(description="null for seed evidence")
    summary: str = Field(min_length=1, max_length=300)
    payload: dict[str, JsonValue] = Field(
        description="Redacted content; one shape per kind (evidence_payload_<kind>.json)"
    )
    suspicious_content: bool
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.purpose == "seed":
            if self.kind != "detector_signal" or self.tool_name is not None:
                raise ValueError("seed evidence is a detector_signal with no tool_name")
        elif self.tool_name is None or TOOL_EVIDENCE_KIND[self.tool_name] != self.kind:
            raise ValueError("tool_name must be the tool that produces this kind")
        try:
            EVIDENCE_PAYLOADS[self.kind].model_validate(self.payload)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise ValueError(
                f"payload does not match kind {self.kind!r}: {where}: {first['msg']}"
            ) from exc
        return self


class TopHypothesis(ContractModel):
    summary: str = Field(min_length=10, max_length=400)
    confidence: float = Field(ge=0, le=1)
    root_cause_category: RootCauseCategory


class Hypothesis(ContractModel):
    """A ranked root-cause hypothesis (§5.4, §5.5, §9.3; DB-004)."""

    id: UUID
    rank: int = Field(ge=1, le=3)
    summary: str = Field(min_length=10, max_length=400)
    root_cause_category: RootCauseCategory
    confidence: float = Field(ge=0, le=1, description="After reflection (AI-016)")
    confidence_initial: float = Field(ge=0, le=1, description="Before reflection")
    evidence_refs: list[EvidenceRef] = Field(min_length=1, max_length=8)
    status: HypothesisStatus
    reflection_notes: str | None = Field(max_length=2000)


# --- Dry runs and proposals (§7.5, §9.3) ---------------------------------------


class WouldChange(ContractModel):
    """One change an action would make: a container, or a cache for ``clear_cache``."""

    target: str = Field(min_length=1, max_length=64)
    change: Literal["start", "stop", "restart", "clear"]


class DryRunCurrent(ContractModel):
    """What the runner sees now (§7.5)."""

    active_release: Semver | None = Field(description="null for clear_cache")
    running_replicas: int | None = Field(ge=0, le=5, description="null for clear_cache")
    containers: list[ContainerState]
    cache_keys: int | None = Field(ge=0, description="clear_cache only")


class DryRunPreconditions(ContractModel):
    rate_limit_ok: bool
    cooldown_ok: bool
    slots_available: bool


class DryRunResult(ContractModel):
    """The runner's preview of an action (RUN-007)."""

    would_change: list[WouldChange]
    current: DryRunCurrent
    preconditions: DryRunPreconditions
    predicted_effect: str = Field(min_length=1, max_length=300)
    warnings: list[str]
    state_fingerprint: Sha256Hex


class RollbackStep(ContractModel):
    """The concrete rollback proposal offered if verification fails (C4, FR-015)."""

    action: EnabledAction
    params: ActionParams


class Proposal(ContractModel):
    """A proposed catalogue action awaiting a decision (§5.6, §9.3; DB-005)."""

    id: UUID
    incident_id: UUID
    kind: ProposalKind
    parent_proposal_id: UUID | None
    action: EnabledAction
    params: ActionParams
    risk_tier: RiskTier = Field(description="Copied from the catalogue, never from the model")
    approval_requirement: ApprovalRequirement
    expected_effect: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=600)
    evidence_refs: list[EvidenceRef] = Field(max_length=8)
    rollback_step: RollbackStep | None = Field(description="null = not reversible")
    dry_run_result: DryRunResult
    dry_run_at: UtcDatetime
    fingerprint: Sha256Hex = Field(description="The app echoes it to approve (§7.5)")
    status: ProposalStatus
    status_reason: str | None = Field(max_length=300)
    expires_at: UtcDatetime
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.kind == "rollback") != (self.parent_proposal_id is not None):
            raise ValueError("a rollback proposal has a parent; a primary proposal has none")
        expected = "tap" if self.risk_tier == "low" else "biometric"
        if self.approval_requirement != expected:
            raise ValueError(f"risk_tier {self.risk_tier!r} requires {expected!r} approval")
        if self.kind == "primary" and not self.evidence_refs:
            raise ValueError("a primary proposal cites at least one evidence ref")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self


# --- Executions (§5.10, §9.3) --------------------------------------------------


class ExecutionStep(ContractModel):
    at: UtcDatetime
    step: str = Field(min_length=1, max_length=100)
    status: StepStatus
    message: str = Field(max_length=300)


class StructuralHealth(ContractModel):
    """The runner's structural health check (RUN-014)."""

    passed: bool
    checked_at: UtcDatetime
    containers: list[ContainerState]
    probes: list[ProbeResult]


class SignalHealth(ContractModel):
    """The worker's signal verification (§5.10 step 7)."""

    passed: bool
    checked_at: UtcDatetime
    window_seconds: int = Field(ge=1)
    signals: list[SignalName] = Field(min_length=1)


class HealthAfter(ContractModel):
    """``executions.health_after``: both checks and the combined verdict (FR-014)."""

    structural: StructuralHealth | None
    signals: SignalHealth | None
    verdict: Literal["pass", "fail"] | None

    @model_validator(mode="after")
    def _verdict_matches(self) -> Self:
        if self.verdict == "pass" and not (
            self.structural and self.structural.passed and self.signals and self.signals.passed
        ):
            raise ValueError("verdict 'pass' needs both checks present and passed")
        if self.verdict == "fail" and not (
            (self.structural and not self.structural.passed)
            or (self.signals and not self.signals.passed)
        ):
            raise ValueError("verdict 'fail' needs a failed check")
        return self


class Execution(ContractModel):
    """One execution of an approved proposal (§5.10, §9.3; DB-007)."""

    id: UUID
    proposal_id: UUID
    status: ExecutionStatus
    steps: list[ExecutionStep]
    error_code: ExecutionErrorCode | None
    health_after: HealthAfter | None
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None

    @model_validator(mode="after")
    def _times_match_status(self) -> Self:
        if (self.status in FINAL_EXECUTION_STATUSES) != (self.finished_at is not None):
            raise ValueError("finished_at is set exactly when the execution has ended")
        if self.status == "queued" and self.started_at is not None:
            raise ValueError("a queued execution has not started")
        return self


# --- Incidents (§8.2, §9.3) ----------------------------------------------------


class AgentMeta(ContractModel):
    """``incidents.agent_meta`` (§6.7, AI-024). Empty in manual mode; filled as the run goes."""

    prompt_version: str | None = Field(default=None, pattern=r"^v[0-9]+$")
    provider: Literal["gemini", "anthropic", "fake"] | None = None
    model: str | None = Field(default=None, max_length=100)
    catalogue_version: Semver | None = None
    agent_config_hash: Sha256Hex | None = None
    tool_calls: int | None = Field(default=None, ge=0, le=8)
    llm_calls: int | None = Field(default=None, ge=0, le=15)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    terminal_state: Literal["PROPOSED", "ESCALATED"] | None = None
    run_started_at: UtcDatetime | None = None


class IncidentSummary(ContractModel):
    """An incident in the feed (§9.3; DB-002)."""

    id: UUID
    service: ServiceName
    severity: Severity
    status: IncidentStatus
    status_reason: EscalationReason | None
    title: str = Field(min_length=1, max_length=200)
    opened_at: UtcDatetime
    resolved_at: UtcDatetime | None
    resolution: Resolution | None
    state_version: int = Field(ge=1)
    top_hypothesis: TopHypothesis | None
    pending_proposal_id: UUID | None

    @model_validator(mode="after")
    def _status_consistent(self) -> Self:
        resolved = self.resolved_at is not None and self.resolution is not None
        if (self.status == "resolved") != resolved:
            raise ValueError("resolved exactly when resolved_at and resolution are set")
        if self.status == "escalated" and self.status_reason is None:
            raise ValueError("an escalated incident needs a status_reason")
        return self


class IncidentDetail(IncidentSummary):
    """One incident with everything the app shows (§9.3, API-005)."""

    window_start: UtcDatetime
    window_end: UtcDatetime | None
    trigger: Trigger
    evidence: list[Evidence]
    hypotheses: list[Hypothesis]
    proposals: list[Proposal]
    executions: list[Execution]
    agent_meta: AgentMeta

    @model_validator(mode="after")
    def _children_consistent(self) -> Self:
        if (self.status == "resolved") != (self.window_end is not None):
            raise ValueError("window_end is set exactly when the incident is resolved")

        refs = [item.ref for item in self.evidence]
        if len(refs) != len(set(refs)):
            raise ValueError("evidence refs must be unique")
        known = set(refs)
        for hypothesis in self.hypotheses:
            if not set(hypothesis.evidence_refs) <= known:
                raise ValueError(f"hypothesis rank {hypothesis.rank} cites unknown evidence")
        for proposal in self.proposals:
            if not set(proposal.evidence_refs) <= known:
                raise ValueError(f"proposal {proposal.id} cites unknown evidence")

        active_ranks = [h.rank for h in self.hypotheses if h.status == "active"]
        if len(active_ranks) != len(set(active_ranks)):
            raise ValueError("active hypothesis ranks must be unique")

        proposal_ids = {proposal.id for proposal in self.proposals}
        if any(proposal.incident_id != self.id for proposal in self.proposals):
            raise ValueError("every proposal belongs to this incident")
        if any(
            p.parent_proposal_id is not None and p.parent_proposal_id not in proposal_ids
            for p in self.proposals
        ):
            raise ValueError("a rollback proposal's parent must be listed")
        pending = [proposal.id for proposal in self.proposals if proposal.status == "pending"]
        if len(pending) > 1:
            raise ValueError("at most one pending proposal per incident")
        if self.pending_proposal_id != (pending[0] if pending else None):
            raise ValueError("pending_proposal_id must name the pending proposal")
        if any(execution.proposal_id not in proposal_ids for execution in self.executions):
            raise ValueError("every execution belongs to a listed proposal")
        return self


# --- Approvals, audit, devices, settings ---------------------------------------


class Approval(ContractModel):
    """A decision on a proposal (§5.9, §8.2; DB-006)."""

    id: UUID
    proposal_id: UUID
    user_id: UUID
    decision: Decision
    reason: str | None = Field(max_length=500)
    auth_method: AuthMethod
    challenge_id: UUID | None
    device_id: UUID | None
    decided_at: UtcDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.decision == "rejected" and (self.reason is None or len(self.reason) < 3):
            raise ValueError("a rejection needs a reason of at least 3 characters")
        if self.decision == "approved" and self.challenge_id is None:
            raise ValueError("an approval needs a challenge_id")
        return self


AuditActor = Annotated[
    str,
    StringConstraints(
        pattern=(
            r"^(user:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
            r"|agent|detector|runner|system)$"
        )
    ),
]


class AuditEntry(ContractModel):
    """One append-only audit row (§7.9; DB-008). ``payload`` is stored already redacted."""

    id: int = Field(ge=1)
    actor: AuditActor
    event: AuditEvent
    ref_type: str | None = Field(max_length=50)
    ref_id: UUID | None
    incident_id: UUID | None
    payload: dict[str, JsonValue]
    created_at: UtcDatetime


class Device(ContractModel):
    """A registered phone (§8.2; DB-010). The FCM token is never returned."""

    id: UUID
    platform: Literal["android"]
    app_version: str | None = Field(max_length=50)
    push_enabled: bool
    last_seen_at: UtcDatetime | None
    created_at: UtcDatetime


ThresholdName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")]


class NotifySettings(ContractModel):
    min_push_severity: Severity


class MonitorSettings(ContractModel):
    """Monitor settings (§8.2 ``monitor_settings``; FR-023, MOB-013).

    ``thresholds`` maps detector setting names (``backend/config/detector.yaml``,
    for example ``error_rate.z``) to values. ``locked`` is true while
    ``BENCHMARK_LOCK`` is on, when ``PUT /settings`` answers ``423``.
    """

    version: int = Field(ge=1)
    services: list[ServiceName] = Field(min_length=1)
    thresholds: dict[ThresholdName, float]
    notify: NotifySettings
    locked: bool
    updated_by: UUID | None
    updated_at: UtcDatetime
