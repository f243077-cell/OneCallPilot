// C1 incidents (domain.py: Signal, Trigger, TopHypothesis, IncidentSummary,
// IncidentDetail). Every key is present in the JSON; `null` means "does not apply".

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';
import 'evidence.dart';
import 'execution.dart';
import 'hypothesis.dart';
import 'proposal.dart';

part 'incident.g.dart';

@JsonSerializable()
class Signal {
  const Signal({
    required this.name,
    required this.value,
    required this.baseline,
    required this.zscore,
    required this.firstAnomalousAt,
  });

  factory Signal.fromJson(Map<String, dynamic> json) => _$SignalFromJson(json);

  @JsonKey(unknownEnumValue: SignalName.unknown)
  final SignalName name;
  final double value;
  final double? baseline;
  final double? zscore;
  final DateTime firstAnomalousAt;
}

@JsonSerializable()
class TriggerUpdate {
  const TriggerUpdate({required this.signals, required this.observedAt});

  factory TriggerUpdate.fromJson(Map<String, dynamic> json) =>
      _$TriggerUpdateFromJson(json);

  final List<Signal> signals;
  final DateTime observedAt;
}

@JsonSerializable()
class Trigger {
  const Trigger({
    required this.source,
    required this.signals,
    required this.observedAt,
    required this.updates,
  });

  factory Trigger.fromJson(Map<String, dynamic> json) =>
      _$TriggerFromJson(json);

  @JsonKey(unknownEnumValue: AlertSource.unknown)
  final AlertSource source;
  final List<Signal> signals;
  final DateTime observedAt;
  final List<TriggerUpdate> updates;
}

@JsonSerializable()
class TopHypothesis {
  const TopHypothesis({
    required this.summary,
    required this.confidence,
    required this.rootCauseCategory,
  });

  factory TopHypothesis.fromJson(Map<String, dynamic> json) =>
      _$TopHypothesisFromJson(json);

  final String summary;
  final double confidence;
  @JsonKey(unknownEnumValue: RootCauseCategory.unknown)
  final RootCauseCategory rootCauseCategory;
}

/// An incident in the feed (§9.3).
@JsonSerializable()
class IncidentSummary {
  const IncidentSummary({
    required this.id,
    required this.service,
    required this.severity,
    required this.status,
    required this.statusReason,
    required this.title,
    required this.openedAt,
    required this.resolvedAt,
    required this.resolution,
    required this.stateVersion,
    required this.topHypothesis,
    required this.pendingProposalId,
  });

  factory IncidentSummary.fromJson(Map<String, dynamic> json) =>
      _$IncidentSummaryFromJson(json);

  final String id;
  @JsonKey(unknownEnumValue: ServiceName.unknown)
  final ServiceName service;
  @JsonKey(unknownEnumValue: Severity.unknown)
  final Severity severity;
  @JsonKey(unknownEnumValue: IncidentStatus.unknown)
  final IncidentStatus status;
  @JsonKey(unknownEnumValue: EscalationReason.unknown)
  final EscalationReason? statusReason;
  final String title;
  final DateTime openedAt;
  final DateTime? resolvedAt;
  @JsonKey(unknownEnumValue: Resolution.unknown)
  final Resolution? resolution;
  final int stateVersion;
  final TopHypothesis? topHypothesis;
  final String? pendingProposalId;
}

/// One incident with everything the app shows (§9.3, API-005).
/// `agent_meta` stays raw: the app does not show it.
@JsonSerializable()
class IncidentDetail extends IncidentSummary {
  const IncidentDetail({
    required super.id,
    required super.service,
    required super.severity,
    required super.status,
    required super.statusReason,
    required super.title,
    required super.openedAt,
    required super.resolvedAt,
    required super.resolution,
    required super.stateVersion,
    required super.topHypothesis,
    required super.pendingProposalId,
    required this.windowStart,
    required this.windowEnd,
    required this.trigger,
    required this.evidence,
    required this.hypotheses,
    required this.proposals,
    required this.executions,
    required this.agentMeta,
  });

  factory IncidentDetail.fromJson(Map<String, dynamic> json) =>
      _$IncidentDetailFromJson(json);

  final DateTime windowStart;
  final DateTime? windowEnd;
  final Trigger trigger;
  final List<Evidence> evidence;
  final List<Hypothesis> hypotheses;
  final List<Proposal> proposals;
  final List<Execution> executions;
  final Map<String, dynamic> agentMeta;
}
