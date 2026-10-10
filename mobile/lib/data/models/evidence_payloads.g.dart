// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'evidence_payloads.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

DetectorSignalPayload _$DetectorSignalPayloadFromJson(
  Map<String, dynamic> json,
) => DetectorSignalPayload(
  service: $enumDecode(
    _$ServiceNameEnumMap,
    json['service'],
    unknownValue: ServiceName.unknown,
  ),
  signals: (json['signals'] as List<dynamic>)
      .map((e) => Signal.fromJson(e as Map<String, dynamic>))
      .toList(),
  observedAt: DateTime.parse(json['observed_at'] as String),
);

const _$ServiceNameEnumMap = {
  ServiceName.api: 'api',
  ServiceName.worker: 'worker',
  ServiceName.lb: 'lb',
  ServiceName.payments: 'payments',
  ServiceName.redis: 'redis',
  ServiceName.postgres: 'postgres',
  ServiceName.unknown: 'unknown',
};

LogLine _$LogLineFromJson(Map<String, dynamic> json) => LogLine(
  ts: DateTime.parse(json['ts'] as String),
  level: $enumDecode(
    _$LogLevelEnumMap,
    json['level'],
    unknownValue: LogLevel.unknown,
  ),
  msg: json['msg'] as String,
  excType: json['exc_type'] as String?,
);

const _$LogLevelEnumMap = {
  LogLevel.debug: 'DEBUG',
  LogLevel.info: 'INFO',
  LogLevel.warning: 'WARNING',
  LogLevel.error: 'ERROR',
  LogLevel.critical: 'CRITICAL',
  LogLevel.unknown: 'UNKNOWN',
};

ExceptionCount _$ExceptionCountFromJson(Map<String, dynamic> json) =>
    ExceptionCount(
      excType: json['exc_type'] as String,
      count: (json['count'] as num).toInt(),
    );

LogQueryPayload _$LogQueryPayloadFromJson(Map<String, dynamic> json) =>
    LogQueryPayload(
      lines: (json['lines'] as List<dynamic>)
          .map((e) => LogLine.fromJson(e as Map<String, dynamic>))
          .toList(),
      totalCount: (json['total_count'] as num).toInt(),
      topExceptionTypes: (json['top_exception_types'] as List<dynamic>)
          .map((e) => ExceptionCount.fromJson(e as Map<String, dynamic>))
          .toList(),
    );

MetricPoint _$MetricPointFromJson(Map<String, dynamic> json) => MetricPoint(
  ts: DateTime.parse(json['ts'] as String),
  value: (json['value'] as num).toDouble(),
);

MetricQueryPayload _$MetricQueryPayloadFromJson(Map<String, dynamic> json) =>
    MetricQueryPayload(
      template: $enumDecode(
        _$MetricTemplateEnumMap,
        json['template'],
        unknownValue: MetricTemplate.unknown,
      ),
      service: $enumDecode(
        _$ServiceNameEnumMap,
        json['service'],
        unknownValue: ServiceName.unknown,
      ),
      stepSeconds: (json['step_seconds'] as num).toInt(),
      points: (json['points'] as List<dynamic>)
          .map((e) => MetricPoint.fromJson(e as Map<String, dynamic>))
          .toList(),
      baselineMean: (json['baseline_mean'] as num?)?.toDouble(),
      peak: (json['peak'] as num?)?.toDouble(),
      pctChange: (json['pct_change'] as num?)?.toDouble(),
      changePointAt: json['change_point_at'] == null
          ? null
          : DateTime.parse(json['change_point_at'] as String),
      note: json['note'] as String?,
    );

const _$MetricTemplateEnumMap = {
  MetricTemplate.errorRate: 'error_rate',
  MetricTemplate.p95Latency: 'p95_latency',
  MetricTemplate.requestRate: 'request_rate',
  MetricTemplate.memoryRss: 'memory_rss',
  MetricTemplate.cpuUsage: 'cpu_usage',
  MetricTemplate.processRestarts: 'process_restarts',
  MetricTemplate.dbPoolInUse: 'db_pool_in_use',
  MetricTemplate.dbPoolWaitP95: 'db_pool_wait_p95',
  MetricTemplate.cacheErrors: 'cache_errors',
  MetricTemplate.upstreamLatencyP95: 'upstream_latency_p95',
  MetricTemplate.jobFailureRate: 'job_failure_rate',
  MetricTemplate.jobLatencyP95: 'job_latency_p95',
  MetricTemplate.unknown: 'unknown',
};

DeployListItem _$DeployListItemFromJson(Map<String, dynamic> json) =>
    DeployListItem(
      service: $enumDecode(
        _$ServiceNameEnumMap,
        json['service'],
        unknownValue: ServiceName.unknown,
      ),
      release: json['release'] as String,
      commitSha: json['commit_sha'] as String,
      commitMessage: json['commit_message'] as String,
      configHash: json['config_hash'] as String,
      deployedAt: DateTime.parse(json['deployed_at'] as String),
      deployedBy: $enumDecode(
        _$DeployedByEnumMap,
        json['deployed_by'],
        unknownValue: DeployedBy.unknown,
      ),
      kind: $enumDecode(
        _$DeployKindEnumMap,
        json['kind'],
        unknownValue: DeployKind.unknown,
      ),
    );

const _$DeployedByEnumMap = {
  DeployedBy.ci: 'ci',
  DeployedBy.runner: 'runner',
  DeployedBy.setup: 'setup',
  DeployedBy.unknown: 'unknown',
};

const _$DeployKindEnumMap = {
  DeployKind.deploy: 'deploy',
  DeployKind.rollback: 'rollback',
  DeployKind.scale: 'scale',
  DeployKind.reset: 'reset',
  DeployKind.unknown: 'unknown',
};

DeployListPayload _$DeployListPayloadFromJson(Map<String, dynamic> json) =>
    DeployListPayload(
      records: (json['records'] as List<dynamic>)
          .map((e) => DeployListItem.fromJson(e as Map<String, dynamic>))
          .toList(),
    );

ContainerHealth _$ContainerHealthFromJson(Map<String, dynamic> json) =>
    ContainerHealth(
      name: json['name'] as String,
      release: json['release'] as String,
      state: $enumDecode(
        _$DockerStateEnumMap,
        json['state'],
        unknownValue: DockerState.unknown,
      ),
      health: $enumDecode(
        _$DockerHealthEnumMap,
        json['health'],
        unknownValue: DockerHealth.unknown,
      ),
      restartCount: (json['restart_count'] as num).toInt(),
      oomKilled: json['oom_killed'] as bool,
      exitCode: (json['exit_code'] as num?)?.toInt(),
      startedAt: json['started_at'] == null
          ? null
          : DateTime.parse(json['started_at'] as String),
    );

const _$DockerStateEnumMap = {
  DockerState.created: 'created',
  DockerState.restarting: 'restarting',
  DockerState.running: 'running',
  DockerState.removing: 'removing',
  DockerState.paused: 'paused',
  DockerState.exited: 'exited',
  DockerState.dead: 'dead',
  DockerState.unknown: 'unknown',
};

const _$DockerHealthEnumMap = {
  DockerHealth.healthy: 'healthy',
  DockerHealth.unhealthy: 'unhealthy',
  DockerHealth.starting: 'starting',
  DockerHealth.none: 'none',
  DockerHealth.unknown: 'unknown',
};

ProbeResult _$ProbeResultFromJson(Map<String, dynamic> json) => ProbeResult(
  url: json['url'] as String,
  ok: json['ok'] as bool,
  statusCode: (json['status_code'] as num?)?.toInt(),
  latencyMs: (json['latency_ms'] as num?)?.toDouble(),
  error: json['error'] as String?,
);

ServiceHealthPayload _$ServiceHealthPayloadFromJson(
  Map<String, dynamic> json,
) => ServiceHealthPayload(
  service: $enumDecode(
    _$ServiceNameEnumMap,
    json['service'],
    unknownValue: ServiceName.unknown,
  ),
  containers: (json['containers'] as List<dynamic>)
      .map((e) => ContainerHealth.fromJson(e as Map<String, dynamic>))
      .toList(),
  probe: json['probe'] == null
      ? null
      : ProbeResult.fromJson(json['probe'] as Map<String, dynamic>),
);

RunbookChunkHit _$RunbookChunkHitFromJson(Map<String, dynamic> json) =>
    RunbookChunkHit(
      source: json['source'] as String,
      heading: json['heading'] as String?,
      content: json['content'] as String,
      similarity: (json['similarity'] as num).toDouble(),
    );

RunbookHitPayload _$RunbookHitPayloadFromJson(Map<String, dynamic> json) =>
    RunbookHitPayload(
      chunks: (json['chunks'] as List<dynamic>)
          .map((e) => RunbookChunkHit.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
