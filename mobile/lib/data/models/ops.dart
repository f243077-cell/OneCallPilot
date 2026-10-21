// C2 shapes of the operations endpoints (api.py: LogTail, AuditEntry,
// AuditPage, MonitorSettings, MonitorSettingsUpdate): the live log tail
// (API-006), the audit history (API-012) and the monitor settings (API-013).

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';
import 'evidence_payloads.dart';

part 'ops.g.dart';

/// `GET /incidents/{id}/log-tail`: up to 50 redacted lines; pass [until] as
/// `since` on the next poll.
@JsonSerializable()
class LogTail {
  const LogTail({required this.lines, required this.until});

  factory LogTail.fromJson(Map<String, dynamic> json) =>
      _$LogTailFromJson(json);

  final List<LogLine> lines;
  final DateTime until;
}

/// One `audit_log` row; its payload is stored already redacted.
@JsonSerializable()
class AuditEntry {
  const AuditEntry({
    required this.id,
    required this.actor,
    required this.event,
    required this.refType,
    required this.refId,
    required this.incidentId,
    required this.payload,
    required this.createdAt,
  });

  factory AuditEntry.fromJson(Map<String, dynamic> json) =>
      _$AuditEntryFromJson(json);

  final int id;

  /// `user:<uuid>`, `agent`, `detector`, `runner` or `system`.
  final String actor;

  /// `approval.approved`, `execution.finished`, …; kept as text so a new
  /// event name still shows.
  final String event;
  final String? refType;
  final String? refId;
  final String? incidentId;
  final Map<String, dynamic> payload;
  final DateTime createdAt;
}

@JsonSerializable()
class AuditPage {
  const AuditPage({required this.items, required this.nextCursor});

  factory AuditPage.fromJson(Map<String, dynamic> json) =>
      _$AuditPageFromJson(json);

  final List<AuditEntry> items;
  final String? nextCursor;
}

@JsonSerializable(createToJson: true)
class NotifySettings {
  const NotifySettings({required this.minPushSeverity});

  factory NotifySettings.fromJson(Map<String, dynamic> json) =>
      _$NotifySettingsFromJson(json);

  @JsonKey(unknownEnumValue: Severity.unknown)
  final Severity minPushSeverity;

  Map<String, dynamic> toJson() => _$NotifySettingsToJson(this);
}

/// `GET /settings`: what the detector watches; read-only while [locked].
@JsonSerializable()
class MonitorSettings {
  const MonitorSettings({
    required this.services,
    required this.thresholds,
    required this.notify,
    required this.version,
    required this.updatedAt,
    required this.updatedBy,
    required this.locked,
    required this.lockReason,
  });

  factory MonitorSettings.fromJson(Map<String, dynamic> json) =>
      _$MonitorSettingsFromJson(json);

  @JsonKey(unknownEnumValue: ServiceName.unknown)
  final List<ServiceName> services;
  final Map<String, double> thresholds;
  final NotifySettings notify;

  /// Sent back on update; a stale one is 409 VERSION_CONFLICT (FR-023).
  final int version;
  final DateTime updatedAt;
  final String? updatedBy;
  final bool locked;
  final String? lockReason;
}

/// Body of `PUT /settings`.
@JsonSerializable(
  createToJson: true,
  createFactory: false,
  explicitToJson: true,
)
class MonitorSettingsUpdate {
  const MonitorSettingsUpdate({
    required this.services,
    required this.thresholds,
    required this.notify,
    required this.version,
  });

  final List<ServiceName> services;
  final Map<String, double> thresholds;
  final NotifySettings notify;
  final int version;

  Map<String, dynamic> toJson() => _$MonitorSettingsUpdateToJson(this);
}
