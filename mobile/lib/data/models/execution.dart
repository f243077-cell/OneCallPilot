// C1 executions (domain.py: Execution, ExecutionStep, HealthAfter).

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';
import 'evidence_payloads.dart';
import 'proposal.dart';

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

/// The runner's structural health check (RUN-014).
@JsonSerializable()
class StructuralHealth {
  const StructuralHealth({
    required this.passed,
    required this.checkedAt,
    required this.containers,
    required this.probes,
  });

  factory StructuralHealth.fromJson(Map<String, dynamic> json) =>
      _$StructuralHealthFromJson(json);

  final bool passed;
  final DateTime checkedAt;
  final List<ContainerState> containers;
  final List<ProbeResult> probes;
}

/// The worker's signal verification (FR-014).
@JsonSerializable()
class SignalHealth {
  const SignalHealth({
    required this.passed,
    required this.checkedAt,
    required this.windowSeconds,
    required this.signals,
  });

  factory SignalHealth.fromJson(Map<String, dynamic> json) =>
      _$SignalHealthFromJson(json);

  final bool passed;
  final DateTime checkedAt;
  final int windowSeconds;
  @JsonKey(unknownEnumValue: SignalName.unknown)
  final List<SignalName> signals;
}

/// Both checks and the combined verdict; a check not run yet is null.
@JsonSerializable()
class HealthAfter {
  const HealthAfter({
    required this.structural,
    required this.signals,
    required this.verdict,
  });

  factory HealthAfter.fromJson(Map<String, dynamic> json) =>
      _$HealthAfterFromJson(json);

  final StructuralHealth? structural;
  final SignalHealth? signals;

  /// `pass`, `fail`, or null while verification runs.
  final String? verdict;
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
  final HealthAfter? healthAfter;
  final DateTime? startedAt;
  final DateTime? finishedAt;
}
