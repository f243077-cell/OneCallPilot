// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'evidence.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

Evidence _$EvidenceFromJson(Map<String, dynamic> json) => Evidence(
  id: json['id'] as String,
  ref: json['ref'] as String,
  kind: $enumDecode(
    _$EvidenceKindEnumMap,
    json['kind'],
    unknownValue: EvidenceKind.unknown,
  ),
  purpose: $enumDecode(
    _$EvidencePurposeEnumMap,
    json['purpose'],
    unknownValue: EvidencePurpose.unknown,
  ),
  toolName: $enumDecodeNullable(
    _$ToolNameEnumMap,
    json['tool_name'],
    unknownValue: ToolName.unknown,
  ),
  summary: json['summary'] as String,
  payload: json['payload'] as Map<String, dynamic>,
  suspiciousContent: json['suspicious_content'] as bool,
  createdAt: DateTime.parse(json['created_at'] as String),
);

const _$EvidenceKindEnumMap = {
  EvidenceKind.detectorSignal: 'detector_signal',
  EvidenceKind.logQuery: 'log_query',
  EvidenceKind.metricQuery: 'metric_query',
  EvidenceKind.deployList: 'deploy_list',
  EvidenceKind.serviceHealth: 'service_health',
  EvidenceKind.runbookHit: 'runbook_hit',
  EvidenceKind.unknown: 'unknown',
};

const _$EvidencePurposeEnumMap = {
  EvidencePurpose.seed: 'seed',
  EvidencePurpose.gather: 'gather',
  EvidencePurpose.disproof: 'disproof',
  EvidencePurpose.unknown: 'unknown',
};

const _$ToolNameEnumMap = {
  ToolName.queryLogs: 'query_logs',
  ToolName.queryMetrics: 'query_metrics',
  ToolName.getRecentDeploys: 'get_recent_deploys',
  ToolName.getServiceHealth: 'get_service_health',
  ToolName.searchRunbooks: 'search_runbooks',
  ToolName.unknown: 'unknown',
};
