// C1 proposals and dry runs (domain.py: Proposal, RollbackStep, DryRunResult).

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';

part 'proposal.g.dart';

@JsonSerializable()
class ContainerState {
  const ContainerState({
    required this.name,
    required this.state,
    required this.health,
  });

  factory ContainerState.fromJson(Map<String, dynamic> json) =>
      _$ContainerStateFromJson(json);

  final String name;
  @JsonKey(unknownEnumValue: DockerState.unknown)
  final DockerState state;
  @JsonKey(unknownEnumValue: DockerHealth.unknown)
  final DockerHealth health;
}

@JsonSerializable()
class WouldChange {
  const WouldChange({required this.target, required this.change});

  factory WouldChange.fromJson(Map<String, dynamic> json) =>
      _$WouldChangeFromJson(json);

  final String target;

  /// `start`, `stop`, `restart` or `clear`.
  final String change;
}

@JsonSerializable()
class DryRunCurrent {
  const DryRunCurrent({
    required this.activeRelease,
    required this.runningReplicas,
    required this.containers,
    required this.cacheKeys,
  });

  factory DryRunCurrent.fromJson(Map<String, dynamic> json) =>
      _$DryRunCurrentFromJson(json);

  final String? activeRelease;
  final int? runningReplicas;
  final List<ContainerState> containers;
  final int? cacheKeys;
}

@JsonSerializable()
class DryRunPreconditions {
  const DryRunPreconditions({
    required this.rateLimitOk,
    required this.cooldownOk,
    required this.slotsAvailable,
  });

  factory DryRunPreconditions.fromJson(Map<String, dynamic> json) =>
      _$DryRunPreconditionsFromJson(json);

  final bool rateLimitOk;
  final bool cooldownOk;
  final bool slotsAvailable;
}

@JsonSerializable()
class DryRunResult {
  const DryRunResult({
    required this.wouldChange,
    required this.current,
    required this.preconditions,
    required this.predictedEffect,
    required this.warnings,
    required this.stateFingerprint,
  });

  factory DryRunResult.fromJson(Map<String, dynamic> json) =>
      _$DryRunResultFromJson(json);

  final List<WouldChange> wouldChange;
  final DryRunCurrent current;
  final DryRunPreconditions preconditions;
  final String predictedEffect;
  final List<String> warnings;
  final String stateFingerprint;
}

@JsonSerializable()
class RollbackStep {
  const RollbackStep({required this.action, required this.params});

  factory RollbackStep.fromJson(Map<String, dynamic> json) =>
      _$RollbackStepFromJson(json);

  @JsonKey(unknownEnumValue: ActionName.unknown)
  final ActionName action;

  /// Parameter values are strings or integers (C4).
  final Map<String, dynamic> params;
}

@JsonSerializable()
class Proposal {
  const Proposal({
    required this.id,
    required this.incidentId,
    required this.kind,
    required this.parentProposalId,
    required this.action,
    required this.params,
    required this.riskTier,
    required this.approvalRequirement,
    required this.expectedEffect,
    required this.rationale,
    required this.evidenceRefs,
    required this.rollbackStep,
    required this.dryRunResult,
    required this.dryRunAt,
    required this.fingerprint,
    required this.status,
    required this.statusReason,
    required this.expiresAt,
    required this.createdAt,
  });

  factory Proposal.fromJson(Map<String, dynamic> json) =>
      _$ProposalFromJson(json);

  final String id;
  final String incidentId;
  @JsonKey(unknownEnumValue: ProposalKind.unknown)
  final ProposalKind kind;
  final String? parentProposalId;
  @JsonKey(unknownEnumValue: ActionName.unknown)
  final ActionName action;
  final Map<String, dynamic> params;
  @JsonKey(unknownEnumValue: RiskTier.unknown)
  final RiskTier riskTier;
  @JsonKey(unknownEnumValue: ApprovalRequirement.unknown)
  final ApprovalRequirement approvalRequirement;
  final String expectedEffect;
  final String rationale;
  final List<String> evidenceRefs;

  /// `null` means the action is not reversible.
  final RollbackStep? rollbackStep;
  final DryRunResult dryRunResult;
  final DateTime dryRunAt;

  /// Echoed in challenge and approve; a mismatch is 409 STALE_PROPOSAL (§7.5).
  final String fingerprint;
  @JsonKey(unknownEnumValue: ProposalStatus.unknown)
  final ProposalStatus status;
  final String? statusReason;
  final DateTime expiresAt;
  final DateTime createdAt;
}
