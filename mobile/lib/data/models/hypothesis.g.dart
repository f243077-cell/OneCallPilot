// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'hypothesis.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

Hypothesis _$HypothesisFromJson(Map<String, dynamic> json) => Hypothesis(
  id: json['id'] as String,
  rank: (json['rank'] as num).toInt(),
  summary: json['summary'] as String,
  rootCauseCategory: $enumDecode(
    _$RootCauseCategoryEnumMap,
    json['root_cause_category'],
    unknownValue: RootCauseCategory.unknown,
  ),
  confidence: (json['confidence'] as num).toDouble(),
  confidenceInitial: (json['confidence_initial'] as num).toDouble(),
  evidenceRefs: (json['evidence_refs'] as List<dynamic>)
      .map((e) => e as String)
      .toList(),
  status: $enumDecode(
    _$HypothesisStatusEnumMap,
    json['status'],
    unknownValue: HypothesisStatus.unknown,
  ),
  reflectionNotes: json['reflection_notes'] as String?,
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

const _$HypothesisStatusEnumMap = {
  HypothesisStatus.active: 'active',
  HypothesisStatus.dropped: 'dropped',
  HypothesisStatus.unknown: 'unknown',
};
