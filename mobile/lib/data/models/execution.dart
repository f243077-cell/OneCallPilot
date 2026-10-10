// C1 executions (domain.py: Execution, ExecutionStep). `health_after` stays raw
// until the Execution Status screen (A2.9) shows it.

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';

part 'execution.g.dart';

@JsonSerializable()
class ExecutionStep {
  const ExecutionStep({
    required this.at,
    required this.step,
    required this.status,
    required this.message,
  });

  factory ExecutionStep.fromJson(Map<String, dynamic> json) =>
      _$ExecutionStepFromJson(json);

  final DateTime at;
  final String step;
  @JsonKey(unknownEnumValue: StepStatus.unknown)
  final StepStatus status;
  final String message;
}

@JsonSerializable()
class Execution {
  const Execution({
    required this.id,
    required this.proposalId,
    required this.status,
    required this.steps,
    required this.errorCode,
    required this.healthAfter,
    required this.startedAt,
    required this.finishedAt,
  });

  factory Execution.fromJson(Map<String, dynamic> json) =>
      _$ExecutionFromJson(json);

  final String id;
  final String proposalId;
  @JsonKey(unknownEnumValue: ExecutionStatus.unknown)
  final ExecutionStatus status;
  final List<ExecutionStep> steps;

  /// A runner error code (C5), or `RUNNER_TIMEOUT`; kept as text.
  final String? errorCode;
  final Map<String, dynamic>? healthAfter;
  final DateTime? startedAt;
  final DateTime? finishedAt;
}
