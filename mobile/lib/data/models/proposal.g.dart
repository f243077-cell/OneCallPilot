// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'proposal.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

ContainerState _$ContainerStateFromJson(Map<String, dynamic> json) =>
    ContainerState(
      name: json['name'] as String,
      state: $enumDecode(
        _$DockerStateEnumMap,
        json['state'],
        unknownValue: DockerState.unknown,
      ),
      health: $enumDecode(
        _$DockerHealthEnumMap,
        json['health'],
        unknownValue: DockerHealth.unknown,
      ),
    );

const _$DockerStateEnumMap = {
  DockerState.created: 'created',
  DockerState.restarting: 'restarting',
  DockerState.running: 'running',
  DockerState.removing: 'removing',
  DockerState.paused: 'paused',
  DockerState.exited: 'exited',
  DockerState.dead: 'dead',
  DockerState.unknown: 'unknown',
};

const _$DockerHealthEnumMap = {
  DockerHealth.healthy: 'healthy',
  DockerHealth.unhealthy: 'unhealthy',
  DockerHealth.starting: 'starting',
  DockerHealth.none: 'none',
  DockerHealth.unknown: 'unknown',
};

WouldChange _$WouldChangeFromJson(Map<String, dynamic> json) => WouldChange(
  target: json['target'] as String,
  change: json['change'] as String,
);

DryRunCurrent _$DryRunCurrentFromJson(Map<String, dynamic> json) =>
    DryRunCurrent(
      activeRelease: json['active_release'] as String?,
      runningReplicas: (json['running_replicas'] as num?)?.toInt(),
      containers: (json['containers'] as List<dynamic>)
          .map((e) => ContainerState.fromJson(e as Map<String, dynamic>))
          .toList(),
      cacheKeys: (json['cache_keys'] as num?)?.toInt(),
    );

DryRunPreconditions _$DryRunPreconditionsFromJson(Map<String, dynamic> json) =>
    DryRunPreconditions(
      rateLimitOk: json['rate_limit_ok'] as bool,
      cooldownOk: json['cooldown_ok'] as bool,
      slotsAvailable: json['slots_available'] as bool,
    );

DryRunResult _$DryRunResultFromJson(Map<String, dynamic> json) => DryRunResult(
  wouldChange: (json['would_change'] as List<dynamic>)
      .map((e) => WouldChange.fromJson(e as Map<String, dynamic>))
      .toList(),
  current: DryRunCurrent.fromJson(json['current'] as Map<String, dynamic>),
  preconditions: DryRunPreconditions.fromJson(
    json['preconditions'] as Map<String, dynamic>,
  ),
  predictedEffect: json['predicted_effect'] as String,
  warnings: (json['warnings'] as List<dynamic>)
      .map((e) => e as String)
      .toList(),
  stateFingerprint: json['state_fingerprint'] as String,
);

RollbackStep _$RollbackStepFromJson(Map<String, dynamic> json) => RollbackStep(
  action: $enumDecode(
    _$ActionNameEnumMap,
    json['action'],
    unknownValue: ActionName.unknown,
  ),
  params: json['params'] as Map<String, dynamic>,
);

const _$ActionNameEnumMap = {
  ActionName.restartService: 'restart_service',
  ActionName.scaleService: 'scale_service',
  ActionName.rollbackDeploy: 'rollback_deploy',
  ActionName.clearCache: 'clear_cache',
  ActionName.unknown: 'unknown',
};

Proposal _$ProposalFromJson(Map<String, dynamic> json) => Proposal(
  id: json['id'] as String,
  incidentId: json['incident_id'] as String,
  kind: $enumDecode(
    _$ProposalKindEnumMap,
    json['kind'],
    unknownValue: ProposalKind.unknown,
  ),
  parentProposalId: json['parent_proposal_id'] as String?,
  action: $enumDecode(
    _$ActionNameEnumMap,
    json['action'],
    unknownValue: ActionName.unknown,
  ),
  params: json['params'] as Map<String, dynamic>,
  riskTier: $enumDecode(
    _$RiskTierEnumMap,
    json['risk_tier'],
    unknownValue: RiskTier.unknown,
  ),
  approvalRequirement: $enumDecode(
    _$ApprovalRequirementEnumMap,
    json['approval_requirement'],
    unknownValue: ApprovalRequirement.unknown,
  ),
  expectedEffect: json['expected_effect'] as String,
  rationale: json['rationale'] as String,
  evidenceRefs: (json['evidence_refs'] as List<dynamic>)
      .map((e) => e as String)
      .toList(),
  rollbackStep: json['rollback_step'] == null
      ? null
      : RollbackStep.fromJson(json['rollback_step'] as Map<String, dynamic>),
  dryRunResult: DryRunResult.fromJson(
    json['dry_run_result'] as Map<String, dynamic>,
  ),
  dryRunAt: DateTime.parse(json['dry_run_at'] as String),
  fingerprint: json['fingerprint'] as String,
  status: $enumDecode(
    _$ProposalStatusEnumMap,
    json['status'],
    unknownValue: ProposalStatus.unknown,
  ),
  statusReason: json['status_reason'] as String?,
  expiresAt: DateTime.parse(json['expires_at'] as String),
  createdAt: DateTime.parse(json['created_at'] as String),
);

const _$ProposalKindEnumMap = {
  ProposalKind.primary: 'primary',
  ProposalKind.rollback: 'rollback',
  ProposalKind.unknown: 'unknown',
};

const _$RiskTierEnumMap = {
  RiskTier.low: 'low',
  RiskTier.medium: 'medium',
  RiskTier.high: 'high',
  RiskTier.unknown: 'unknown',
};

const _$ApprovalRequirementEnumMap = {
  ApprovalRequirement.tap: 'tap',
  ApprovalRequirement.biometric: 'biometric',
  ApprovalRequirement.unknown: 'unknown',
};

const _$ProposalStatusEnumMap = {
  ProposalStatus.pending: 'pending',
  ProposalStatus.approved: 'approved',
  ProposalStatus.rejected: 'rejected',
  ProposalStatus.expired: 'expired',
  ProposalStatus.superseded: 'superseded',
  ProposalStatus.executing: 'executing',
  ProposalStatus.succeeded: 'succeeded',
  ProposalStatus.failed: 'failed',
  ProposalStatus.unknown: 'unknown',
};
