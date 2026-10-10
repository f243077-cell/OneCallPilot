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
  healthAfter: json['health_after'] as Map<String, dynamic>?,
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
