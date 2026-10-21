// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'execution.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

ExecutionStep _$ExecutionStepFromJson(Map<String, dynamic> json) =>
    ExecutionStep(
      at: DateTime.parse(json['at'] as String),
      step: json['step'] as String,
      status: $enumDecode(
        _$StepStatusEnumMap,
        json['status'],
        unknownValue: StepStatus.unknown,
      ),
      message: json['message'] as String,
    );

const _$StepStatusEnumMap = {
  StepStatus.running: 'running',
  StepStatus.succeeded: 'succeeded',
  StepStatus.failed: 'failed',
  StepStatus.unknown: 'unknown',
};

StructuralHealth _$StructuralHealthFromJson(Map<String, dynamic> json) =>
    StructuralHealth(
      passed: json['passed'] as bool,
      checkedAt: DateTime.parse(json['checked_at'] as String),
      containers: (json['containers'] as List<dynamic>)
          .map((e) => ContainerState.fromJson(e as Map<String, dynamic>))
          .toList(),
      probes: (json['probes'] as List<dynamic>)
          .map((e) => ProbeResult.fromJson(e as Map<String, dynamic>))
          .toList(),
    );

SignalHealth _$SignalHealthFromJson(Map<String, dynamic> json) => SignalHealth(
  passed: json['passed'] as bool,
  checkedAt: DateTime.parse(json['checked_at'] as String),
  windowSeconds: (json['window_seconds'] as num).toInt(),
  signals: (json['signals'] as List<dynamic>)
      .map(
        (e) => $enumDecode(
          _$SignalNameEnumMap,
          e,
          unknownValue: SignalName.unknown,
        ),
      )
      .toList(),
);

const _$SignalNameEnumMap = {
  SignalName.errorRate: 'error_rate',
  SignalName.p95Latency: 'p95_latency',
  SignalName.jobFailureRate: 'job_failure_rate',
  SignalName.restarts: 'restarts',
  SignalName.stackTraces: 'stack_traces',
  SignalName.unknown: 'unknown',
};

HealthAfter _$HealthAfterFromJson(Map<String, dynamic> json) => HealthAfter(
  structural: json['structural'] == null
      ? null
      : StructuralHealth.fromJson(json['structural'] as Map<String, dynamic>),
  signals: json['signals'] == null
      ? null
      : SignalHealth.fromJson(json['signals'] as Map<String, dynamic>),
  verdict: json['verdict'] as String?,
);

Execution _$ExecutionFromJson(Map<String, dynamic> json) => Execution(
  id: json['id'] as String,
  proposalId: json['proposal_id'] as String,
  status: $enumDecode(
    _$ExecutionStatusEnumMap,
    json['status'],
    unknownValue: ExecutionStatus.unknown,
  ),
  steps: (json['steps'] as List<dynamic>)
      .map((e) => ExecutionStep.fromJson(e as Map<String, dynamic>))
      .toList(),
  errorCode: json['error_code'] as String?,
  healthAfter: json['health_after'] == null
      ? null
      : HealthAfter.fromJson(json['health_after'] as Map<String, dynamic>),
  startedAt: json['started_at'] == null
      ? null
      : DateTime.parse(json['started_at'] as String),
  finishedAt: json['finished_at'] == null
      ? null
      : DateTime.parse(json['finished_at'] as String),
);

const _$ExecutionStatusEnumMap = {
  ExecutionStatus.queued: 'queued',
  ExecutionStatus.running: 'running',
  ExecutionStatus.succeeded: 'succeeded',
  ExecutionStatus.failed: 'failed',
  ExecutionStatus.aborted: 'aborted',
  ExecutionStatus.unknown: 'unknown',
};
