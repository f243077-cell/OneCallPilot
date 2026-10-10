// C1 evidence payloads, one shape per `Evidence.kind` (domain.py:
// DetectorSignalPayload, LogQueryPayload, MetricQueryPayload,
// DeployListPayload, ServiceHealthPayload, RunbookHitPayload).

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';
import 'evidence.dart';
import 'incident.dart';

part 'evidence_payloads.g.dart';

sealed class EvidencePayload {
  const EvidencePayload();
}

/// The typed payload of [evidence], or null when this build does not know
/// the kind or the payload does not have the kind's shape.
EvidencePayload? parsePayload(Evidence evidence) {
  final json = evidence.payload;
  try {
    return switch (evidence.kind) {
      EvidenceKind.detectorSignal => DetectorSignalPayload.fromJson(json),
      EvidenceKind.logQuery => LogQueryPayload.fromJson(json),
      EvidenceKind.metricQuery => MetricQueryPayload.fromJson(json),
      EvidenceKind.deployList => DeployListPayload.fromJson(json),
      EvidenceKind.serviceHealth => ServiceHealthPayload.fromJson(json),
      EvidenceKind.runbookHit => RunbookHitPayload.fromJson(json),
      EvidenceKind.unknown => null,
    };
  } on TypeError {
    return null; // a field of the wrong type, or a missing one
  } on ArgumentError {
    return null; // a missing enum value
  } on FormatException {
    return null; // an unparseable timestamp
  }
}

@JsonSerializable()
class DetectorSignalPayload extends EvidencePayload {
  const DetectorSignalPayload({
    required this.service,
    required this.signals,
    required this.observedAt,
  });

  factory DetectorSignalPayload.fromJson(Map<String, dynamic> json) =>
      _$DetectorSignalPayloadFromJson(json);

  @JsonKey(unknownEnumValue: ServiceName.unknown)
  final ServiceName service;
  final List<Signal> signals;
  final DateTime observedAt;
}

@JsonSerializable()
class LogLine {
  const LogLine({
    required this.ts,
    required this.level,
    required this.msg,
    required this.excType,
  });

  factory LogLine.fromJson(Map<String, dynamic> json) =>
      _$LogLineFromJson(json);

  final DateTime ts;
  @JsonKey(unknownEnumValue: LogLevel.unknown)
  final LogLevel level;
  final String msg;
  final String? excType;
}

@JsonSerializable()
class ExceptionCount {
  const ExceptionCount({required this.excType, required this.count});

  factory ExceptionCount.fromJson(Map<String, dynamic> json) =>
      _$ExceptionCountFromJson(json);

  final String excType;
  final int count;
}

@JsonSerializable()
class LogQueryPayload extends EvidencePayload {
  const LogQueryPayload({
    required this.lines,
    required this.totalCount,
    required this.topExceptionTypes,
  });

  factory LogQueryPayload.fromJson(Map<String, dynamic> json) =>
      _$LogQueryPayloadFromJson(json);

  final List<LogLine> lines;

  /// Matching lines in the window, before the 50-line cap.
  final int totalCount;
  final List<ExceptionCount> topExceptionTypes;
}

@JsonSerializable()
class MetricPoint {
  const MetricPoint({required this.ts, required this.value});

  factory MetricPoint.fromJson(Map<String, dynamic> json) =>
      _$MetricPointFromJson(json);

  final DateTime ts;
  final double value;
}

@JsonSerializable()
class MetricQueryPayload extends EvidencePayload {
  const MetricQueryPayload({
    required this.template,
    required this.service,
    required this.stepSeconds,
    required this.points,
    required this.baselineMean,
    required this.peak,
    required this.pctChange,
    required this.changePointAt,
    required this.note,
  });

  factory MetricQueryPayload.fromJson(Map<String, dynamic> json) =>
      _$MetricQueryPayloadFromJson(json);

  @JsonKey(unknownEnumValue: MetricTemplate.unknown)
  final MetricTemplate template;
  @JsonKey(unknownEnumValue: ServiceName.unknown)
  final ServiceName service;
  final int stepSeconds;
  final List<MetricPoint> points;
  final double? baselineMean;
  final double? peak;
  final double? pctChange;
  final DateTime? changePointAt;

  /// For example, why the series is empty.
  final String? note;
}

@JsonSerializable()
class DeployListItem {
  const DeployListItem({
    required this.service,
    required this.release,
    required this.commitSha,
    required this.commitMessage,
    required this.configHash,
    required this.deployedAt,
    required this.deployedBy,
    required this.kind,
  });

  factory DeployListItem.fromJson(Map<String, dynamic> json) =>
      _$DeployListItemFromJson(json);

  @JsonKey(unknownEnumValue: ServiceName.unknown)
  final ServiceName service;
  final String release;
  final String commitSha;
  final String commitMessage;
  final String configHash;
  final DateTime deployedAt;
  @JsonKey(unknownEnumValue: DeployedBy.unknown)
  final DeployedBy deployedBy;
  @JsonKey(unknownEnumValue: DeployKind.unknown)
  final DeployKind kind;
}

@JsonSerializable()
class DeployListPayload extends EvidencePayload {
  const DeployListPayload({required this.records});

  factory DeployListPayload.fromJson(Map<String, dynamic> json) =>
      _$DeployListPayloadFromJson(json);

  /// Newest first.
  final List<DeployListItem> records;
}

@JsonSerializable()
class ContainerHealth {
  const ContainerHealth({
    required this.name,
    required this.release,
    required this.state,
    required this.health,
    required this.restartCount,
    required this.oomKilled,
    required this.exitCode,
    required this.startedAt,
  });

  factory ContainerHealth.fromJson(Map<String, dynamic> json) =>
      _$ContainerHealthFromJson(json);

  final String name;
  final String release;
  @JsonKey(unknownEnumValue: DockerState.unknown)
  final DockerState state;
  @JsonKey(unknownEnumValue: DockerHealth.unknown)
  final DockerHealth health;
  final int restartCount;
  final bool oomKilled;
  final int? exitCode;
  final DateTime? startedAt;
}

@JsonSerializable()
class ProbeResult {
  const ProbeResult({
    required this.url,
    required this.ok,
    required this.statusCode,
    required this.latencyMs,
    required this.error,
  });

  factory ProbeResult.fromJson(Map<String, dynamic> json) =>
      _$ProbeResultFromJson(json);

  final String url;
  final bool ok;
  final int? statusCode;
  final double? latencyMs;
  final String? error;
}

@JsonSerializable()
class ServiceHealthPayload extends EvidencePayload {
  const ServiceHealthPayload({
    required this.service,
    required this.containers,
    required this.probe,
  });

  factory ServiceHealthPayload.fromJson(Map<String, dynamic> json) =>
      _$ServiceHealthPayloadFromJson(json);

  @JsonKey(unknownEnumValue: ServiceName.unknown)
  final ServiceName service;
  final List<ContainerHealth> containers;
  final ProbeResult? probe;
}

@JsonSerializable()
class RunbookChunkHit {
  const RunbookChunkHit({
    required this.source,
    required this.heading,
    required this.content,
    required this.similarity,
  });

  factory RunbookChunkHit.fromJson(Map<String, dynamic> json) =>
      _$RunbookChunkHitFromJson(json);

  final String source;
  final String? heading;
  final String content;
  final double similarity;
}

@JsonSerializable()
class RunbookHitPayload extends EvidencePayload {
  const RunbookHitPayload({required this.chunks});

  factory RunbookHitPayload.fromJson(Map<String, dynamic> json) =>
      _$RunbookHitPayloadFromJson(json);

  final List<RunbookChunkHit> chunks;
}
