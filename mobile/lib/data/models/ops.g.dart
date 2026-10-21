// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'ops.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

LogTail _$LogTailFromJson(Map<String, dynamic> json) => LogTail(
  lines: (json['lines'] as List<dynamic>)
      .map((e) => LogLine.fromJson(e as Map<String, dynamic>))
      .toList(),
  until: DateTime.parse(json['until'] as String),
);

AuditEntry _$AuditEntryFromJson(Map<String, dynamic> json) => AuditEntry(
  id: (json['id'] as num).toInt(),
  actor: json['actor'] as String,
  event: json['event'] as String,
  refType: json['ref_type'] as String?,
  refId: json['ref_id'] as String?,
  incidentId: json['incident_id'] as String?,
  payload: json['payload'] as Map<String, dynamic>,
  createdAt: DateTime.parse(json['created_at'] as String),
);

AuditPage _$AuditPageFromJson(Map<String, dynamic> json) => AuditPage(
  items: (json['items'] as List<dynamic>)
      .map((e) => AuditEntry.fromJson(e as Map<String, dynamic>))
      .toList(),
  nextCursor: json['next_cursor'] as String?,
);

NotifySettings _$NotifySettingsFromJson(Map<String, dynamic> json) =>
    NotifySettings(
      minPushSeverity: $enumDecode(
        _$SeverityEnumMap,
        json['min_push_severity'],
        unknownValue: Severity.unknown,
      ),
    );

Map<String, dynamic> _$NotifySettingsToJson(NotifySettings instance) =>
    <String, dynamic>{
      'min_push_severity': _$SeverityEnumMap[instance.minPushSeverity]!,
    };

const _$SeverityEnumMap = {
  Severity.sev1: 'sev1',
  Severity.sev2: 'sev2',
  Severity.sev3: 'sev3',
  Severity.unknown: 'unknown',
};

MonitorSettings _$MonitorSettingsFromJson(Map<String, dynamic> json) =>
    MonitorSettings(
      services: (json['services'] as List<dynamic>)
          .map(
            (e) => $enumDecode(
              _$ServiceNameEnumMap,
              e,
              unknownValue: ServiceName.unknown,
            ),
          )
          .toList(),
      thresholds: (json['thresholds'] as Map<String, dynamic>).map(
        (k, e) => MapEntry(k, (e as num).toDouble()),
      ),
      notify: NotifySettings.fromJson(json['notify'] as Map<String, dynamic>),
      version: (json['version'] as num).toInt(),
      updatedAt: DateTime.parse(json['updated_at'] as String),
      updatedBy: json['updated_by'] as String?,
      locked: json['locked'] as bool,
      lockReason: json['lock_reason'] as String?,
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

Map<String, dynamic> _$MonitorSettingsUpdateToJson(
  MonitorSettingsUpdate instance,
) => <String, dynamic>{
  'services': instance.services.map((e) => _$ServiceNameEnumMap[e]!).toList(),
  'thresholds': instance.thresholds,
  'notify': instance.notify.toJson(),
  'version': instance.version,
};
