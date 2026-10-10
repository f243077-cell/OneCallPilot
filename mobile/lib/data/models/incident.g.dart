// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'incident.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

Signal _$SignalFromJson(Map<String, dynamic> json) => Signal(
  name: $enumDecode(
    _$SignalNameEnumMap,
    json['name'],
    unknownValue: SignalName.unknown,
  ),
  value: (json['value'] as num).toDouble(),
  baseline: (json['baseline'] as num?)?.toDouble(),
  zscore: (json['zscore'] as num?)?.toDouble(),
  firstAnomalousAt: DateTime.parse(json['first_anomalous_at'] as String),
);

const _$SignalNameEnumMap = {
  SignalName.errorRate: 'error_rate',
  SignalName.p95Latency: 'p95_latency',
  SignalName.jobFailureRate: 'job_failure_rate',
  SignalName.restarts: 'restarts',
  SignalName.stackTraces: 'stack_traces',
  SignalName.unknown: 'unknown',
};

TriggerUpdate _$TriggerUpdateFromJson(Map<String, dynamic> json) =>
    TriggerUpdate(
      signals: (json['signals'] as List<dynamic>)
          .map((e) => Signal.fromJson(e as Map<String, dynamic>))
          .toList(),
      observedAt: DateTime.parse(json['observed_at'] as String),
    );

Trigger _$TriggerFromJson(Map<String, dynamic> json) => Trigger(
  source: $enumDecode(
    _$AlertSourceEnumMap,
    json['source'],
    unknownValue: AlertSource.unknown,
  ),
  signals: (json['signals'] as List<dynamic>)
      .map((e) => Signal.fromJson(e as Map<String, dynamic>))
      .toList(),
  observedAt: DateTime.parse(json['observed_at'] as String),
  updates: (json['updates'] as List<dynamic>)
      .map((e) => TriggerUpdate.fromJson(e as Map<String, dynamic>))
      .toList(),
);

const _$AlertSourceEnumMap = {
  AlertSource.detector: 'detector',
  AlertSource.alertmanager: 'alertmanager',
  AlertSource.unknown: 'unknown',
};

TopHypothesis _$TopHypothesisFromJson(Map<String, dynamic> json) =>
    TopHypothesis(
      summary: json['summary'] as String,
      confidence: (json['confidence'] as num).toDouble(),
      rootCauseCategory: $enumDecode(
        _$RootCauseCategoryEnumMap,
        json['root_cause_category'],
        unknownValue: RootCauseCategory.unknown,
      ),
    );

const _$RootCauseCategoryEnumMap = {
  RootCauseCategory.badDeploy: 'bad_deploy',
  RootCauseCategory.configError: 'config_error',
  RootCauseCategory.memoryLeak: 'memory_leak',
  RootCauseCategory.resourceSaturation: 'resource_saturation',
  RootCauseCategory.trafficSurge: 'traffic_surge',
  RootCauseCategory.dependencyUnavailable: 'dependency_unavailable',
  RootCauseCategory.dependencySlow: 'dependency_slow',
  RootCauseCategory.dbConnectionExhaustion: 'db_connection_exhaustion',
  RootCauseCategory.cacheFailure: 'cache_failure',
  RootCauseCategory.applicationBug: 'application_bug',
  RootCauseCategory.networkIssue: 'network_issue',
  RootCauseCategory.diskPressure: 'disk_pressure',
  RootCauseCategory.unknown: 'unknown',
};

IncidentSummary _$IncidentSummaryFromJson(Map<String, dynamic> json) =>
    IncidentSummary(
      id: json['id'] as String,
      service: $enumDecode(
        _$ServiceNameEnumMap,
        json['service'],
        unknownValue: ServiceName.unknown,
      ),
      severity: $enumDecode(
        _$SeverityEnumMap,
        json['severity'],
        unknownValue: Severity.unknown,
      ),
      status: $enumDecode(
        _$IncidentStatusEnumMap,
        json['status'],
        unknownValue: IncidentStatus.unknown,
      ),
      statusReason: $enumDecodeNullable(
        _$EscalationReasonEnumMap,
        json['status_reason'],
        unknownValue: EscalationReason.unknown,
      ),
      title: json['title'] as String,
      openedAt: DateTime.parse(json['opened_at'] as String),
      resolvedAt: json['resolved_at'] == null
          ? null
          : DateTime.parse(json['resolved_at'] as String),
      resolution: $enumDecodeNullable(
        _$ResolutionEnumMap,
        json['resolution'],
        unknownValue: Resolution.unknown,
      ),
      stateVersion: (json['state_version'] as num).toInt(),
      topHypothesis: json['top_hypothesis'] == null
          ? null
          : TopHypothesis.fromJson(
              json['top_hypothesis'] as Map<String, dynamic>,
            ),
      pendingProposalId: json['pending_proposal_id'] as String?,
    );

const _$ServiceNameEnumMap = {
  ServiceName.api: 'api',
  ServiceName.worker: 'worker',
  ServiceName.lb: 'lb',
  ServiceName.payments: 'payments',
  ServiceName.redis: 'redis',
  ServiceName.postgres: 'postgres',
  ServiceName.unknown: 'unknown',
};

const _$SeverityEnumMap = {
  Severity.sev1: 'sev1',
  Severity.sev2: 'sev2',
  Severity.sev3: 'sev3',
  Severity.unknown: 'unknown',
};

const _$IncidentStatusEnumMap = {
  IncidentStatus.investigating: 'investigating',
  IncidentStatus.awaitingApproval: 'awaiting_approval',
  IncidentStatus.escalated: 'escalated',
  IncidentStatus.executing: 'executing',
  IncidentStatus.verifying: 'verifying',
  IncidentStatus.resolved: 'resolved',
  IncidentStatus.actionFailed: 'action_failed',
  IncidentStatus.unknown: 'unknown',
};

const _$EscalationReasonEnumMap = {
  EscalationReason.lowConfidence: 'low_confidence',
  EscalationReason.noCatalogueActionFits: 'no_catalogue_action_fits',
  EscalationReason.budgetExhausted: 'budget_exhausted',
  EscalationReason.llmUnavailable: 'llm_unavailable',
  EscalationReason.llmOutputInvalid: 'llm_output_invalid',
  EscalationReason.proposalInvalid: 'proposal_invalid',
  EscalationReason.dryRunUnavailable: 'dry_run_unavailable',
  EscalationReason.proposalRejected: 'proposal_rejected',
  EscalationReason.proposalExpired: 'proposal_expired',
  EscalationReason.rolledBackAfterFailedAction:
      'rolled_back_after_failed_action',
  EscalationReason.runnerRefused: 'runner_refused',
  EscalationReason.runnerTimeout: 'runner_timeout',
  EscalationReason.agentInterrupted: 'agent_interrupted',
  EscalationReason.unknown: 'unknown',
};

const _$ResolutionEnumMap = {
  Resolution.actionSucceeded: 'action_succeeded',
  Resolution.autoRecovered: 'auto_recovered',
  Resolution.manual: 'manual',
  Resolution.rolledBack: 'rolled_back',
  Resolution.unknown: 'unknown',
};

IncidentDetail _$IncidentDetailFromJson(Map<String, dynamic> json) =>
    IncidentDetail(
      id: json['id'] as String,
      service: $enumDecode(
        _$ServiceNameEnumMap,
        json['service'],
        unknownValue: ServiceName.unknown,
      ),
      severity: $enumDecode(
        _$SeverityEnumMap,
        json['severity'],
        unknownValue: Severity.unknown,
      ),
      status: $enumDecode(
        _$IncidentStatusEnumMap,
        json['status'],
        unknownValue: IncidentStatus.unknown,
      ),
      statusReason: $enumDecodeNullable(
        _$EscalationReasonEnumMap,
        json['status_reason'],
        unknownValue: EscalationReason.unknown,
      ),
      title: json['title'] as String,
      openedAt: DateTime.parse(json['opened_at'] as String),
      resolvedAt: json['resolved_at'] == null
          ? null
          : DateTime.parse(json['resolved_at'] as String),
      resolution: $enumDecodeNullable(
        _$ResolutionEnumMap,
        json['resolution'],
        unknownValue: Resolution.unknown,
      ),
      stateVersion: (json['state_version'] as num).toInt(),
      topHypothesis: json['top_hypothesis'] == null
          ? null
          : TopHypothesis.fromJson(
              json['top_hypothesis'] as Map<String, dynamic>,
            ),
      pendingProposalId: json['pending_proposal_id'] as String?,
      windowStart: DateTime.parse(json['window_start'] as String),
      windowEnd: json['window_end'] == null
          ? null
          : DateTime.parse(json['window_end'] as String),
      trigger: Trigger.fromJson(json['trigger'] as Map<String, dynamic>),
      evidence: (json['evidence'] as List<dynamic>)
          .map((e) => Evidence.fromJson(e as Map<String, dynamic>))
          .toList(),
      hypotheses: (json['hypotheses'] as List<dynamic>)
          .map((e) => Hypothesis.fromJson(e as Map<String, dynamic>))
          .toList(),
      proposals: (json['proposals'] as List<dynamic>)
          .map((e) => Proposal.fromJson(e as Map<String, dynamic>))
          .toList(),
      executions: (json['executions'] as List<dynamic>)
          .map((e) => Execution.fromJson(e as Map<String, dynamic>))
          .toList(),
      agentMeta: json['agent_meta'] as Map<String, dynamic>,
    );
