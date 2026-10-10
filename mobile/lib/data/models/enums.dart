// Enumerations of C1 (contracts/python/oncallpilot_contracts/enums.py).
// Every enum has an `unknown` case: a value this build does not know maps to
// it instead of failing (MOB-020). Fields use `unknownEnumValue: X.unknown`.

import 'package:json_annotation/json_annotation.dart';

@JsonEnum(fieldRename: FieldRename.snake)
enum IncidentStatus {
  investigating,
  awaitingApproval,
  escalated,
  executing,
  verifying,
  resolved,
  actionFailed,
  unknown,
}

enum Severity { sev1, sev2, sev3, unknown }

@JsonEnum(fieldRename: FieldRename.snake)
enum Resolution { actionSucceeded, autoRecovered, manual, rolledBack, unknown }

enum ServiceName { api, worker, lb, payments, redis, postgres, unknown }

@JsonEnum(fieldRename: FieldRename.snake)
enum EscalationReason {
  lowConfidence,
  noCatalogueActionFits,
  budgetExhausted,
  llmUnavailable,
  llmOutputInvalid,
  proposalInvalid,
  dryRunUnavailable,
  proposalRejected,
  proposalExpired,
  rolledBackAfterFailedAction,
  runnerRefused,
  runnerTimeout,
  agentInterrupted,
  unknown,
}

@JsonEnum(fieldRename: FieldRename.snake)
enum SignalName {
  errorRate,
  p95Latency,
  jobFailureRate,
  restarts,
  stackTraces,
  unknown,
}

enum AlertSource { detector, alertmanager, unknown }

@JsonEnum(fieldRename: FieldRename.snake)
enum EvidenceKind {
  detectorSignal,
  logQuery,
  metricQuery,
  deployList,
  serviceHealth,
  runbookHit,
  unknown,
}

enum EvidencePurpose { seed, gather, disproof, unknown }

@JsonEnum(fieldRename: FieldRename.snake)
enum ToolName {
  queryLogs,
  queryMetrics,
  getRecentDeploys,
  getServiceHealth,
  searchRunbooks,
  unknown,
}

@JsonEnum(fieldRename: FieldRename.snake)
enum RootCauseCategory {
  badDeploy,
  configError,
  memoryLeak,
  resourceSaturation,
  trafficSurge,
  dependencyUnavailable,
  dependencySlow,
  dbConnectionExhaustion,
  cacheFailure,
  applicationBug,
  networkIssue,
  diskPressure,
  unknown,
}

enum HypothesisStatus { active, dropped, unknown }

/// C4 enabled actions.
@JsonEnum(fieldRename: FieldRename.snake)
enum ActionName {
  restartService,
  scaleService,
  rollbackDeploy,
  clearCache,
  unknown,
}

enum RiskTier { low, medium, high, unknown }

enum ApprovalRequirement { tap, biometric, unknown }

enum AuthMethod { biometric, tap }

enum ProposalKind { primary, rollback, unknown }

enum ProposalStatus {
  pending,
  approved,
  rejected,
  expired,
  superseded,
  executing,
  succeeded,
  failed,
  unknown,
}

enum ExecutionStatus { queued, running, succeeded, failed, aborted, unknown }

enum StepStatus { running, succeeded, failed, unknown }

enum DockerState {
  created,
  restarting,
  running,
  removing,
  paused,
  exited,
  dead,
  unknown,
}

enum DockerHealth { healthy, unhealthy, starting, none, unknown }

/// C3 event names.
enum WsEventName {
  @JsonValue('incident.opened')
  incidentOpened,
  @JsonValue('incident.updated')
  incidentUpdated,
  @JsonValue('evidence.added')
  evidenceAdded,
  @JsonValue('hypothesis.updated')
  hypothesisUpdated,
  @JsonValue('proposal.created')
  proposalCreated,
  @JsonValue('proposal.updated')
  proposalUpdated,
  @JsonValue('execution.progress')
  executionProgress,
  @JsonValue('incident.resolved')
  incidentResolved,
  unknown,
}
