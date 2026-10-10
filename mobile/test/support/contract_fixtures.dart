// Test support for the contract fixture test (Phase 0 "Dart parses the fixtures",
// MOB-020, TEST-009). Not a contract: it reads contracts/fixtures/ and checks the
// shapes the app relies on. The real DTOs (json_serializable) arrive in A1.5/A1.6
// and replace these stub parsers.

import 'dart:convert';
import 'dart:io';

/// The fixtures folder: `$OCP_CONTRACTS_DIR/fixtures` when set (for example a
/// worktree of another branch), otherwise `../contracts/fixtures` from `mobile/`.
Directory contractFixturesDir() {
  final override = Platform.environment['OCP_CONTRACTS_DIR'];
  final base = (override != null && override.isNotEmpty)
      ? override
      : '../contracts';
  return Directory('$base/fixtures');
}

/// True when the C1/C3 fixtures (timelines and WebSocket messages) are present.
/// The repository skeleton has both folders with only a .gitkeep, so look for JSON.
bool hasSharedFixtures(Directory dir) =>
    jsonFiles(Directory('${dir.path}/timelines')).isNotEmpty &&
    jsonFiles(Directory('${dir.path}/ws')).isNotEmpty;

List<File> jsonFiles(Directory dir) {
  if (!dir.existsSync()) return [];
  final files =
      dir
          .listSync()
          .whereType<File>()
          .where((f) => f.path.endsWith('.json'))
          .toList()
        ..sort((a, b) => a.path.compareTo(b.path));
  return files;
}

Map<String, dynamic> readJsonObject(File file) {
  final decoded = jsonDecode(file.readAsStringSync());
  if (decoded is! Map<String, dynamic>) {
    throw FormatException('${file.path}: top level is not a JSON object');
  }
  return decoded;
}

// --- enums: an unknown value maps to `unknown` instead of failing (MOB-020) ---

enum WsEventName {
  incidentOpened('incident.opened'),
  incidentUpdated('incident.updated'),
  evidenceAdded('evidence.added'),
  hypothesisUpdated('hypothesis.updated'),
  proposalCreated('proposal.created'),
  proposalUpdated('proposal.updated'),
  executionProgress('execution.progress'),
  incidentResolved('incident.resolved'),
  unknown('unknown');

  const WsEventName(this.wire);
  final String wire;

  static WsEventName parse(String value) =>
      values.firstWhere((e) => e.wire == value, orElse: () => unknown);
}

enum IncidentStatus {
  investigating,
  awaitingApproval,
  escalated,
  executing,
  verifying,
  resolved,
  actionFailed,
  unknown;

  static IncidentStatus parse(String value) {
    final camel = value.replaceAllMapped(
      RegExp(r'_([a-z])'),
      (m) => m[1]!.toUpperCase(),
    );
    return values.firstWhere((e) => e.name == camel, orElse: () => unknown);
  }
}

// --- stub parsers: required keys and types, cross-references ---

final _evidenceRef = RegExp(r'^E[0-9]{1,3}$');
final _sha256 = RegExp(r'^[0-9a-f]{64}$');

T req<T>(Map<String, dynamic> json, String key, String where) {
  if (!json.containsKey(key)) throw FormatException('$where: missing "$key"');
  final value = json[key];
  if (value is! T) {
    throw FormatException(
      '$where: "$key" is ${value.runtimeType}, expected $T',
    );
  }
  return value;
}

DateTime reqTime(Map<String, dynamic> json, String key, String where) {
  final raw = req<String>(json, key, where);
  final value = DateTime.parse(raw);
  if (!value.isUtc) throw FormatException('$where: "$key" is not UTC');
  return value;
}

/// Parses an IncidentDetail (C1) far enough for the Incident Detail and Action
/// Review screens: every cited `E#` exists, proposals carry a fingerprint and expiry.
void parseIncidentDetail(Map<String, dynamic> json, String where) {
  req<String>(json, 'id', where);
  IncidentStatus.parse(req<String>(json, 'status', where));
  req<String>(json, 'title', where);
  reqTime(json, 'opened_at', where);
  req<int>(json, 'state_version', where);
  final refs = <String>{};
  for (final item in req<List<dynamic>>(
    json,
    'evidence',
    where,
  ).cast<Map<String, dynamic>>()) {
    final ref = req<String>(item, 'ref', '$where.evidence');
    if (!_evidenceRef.hasMatch(ref)) {
      throw FormatException('$where: bad evidence ref $ref');
    }
    req<String>(item, 'kind', '$where.evidence[$ref]');
    req<Map<String, dynamic>>(item, 'payload', '$where.evidence[$ref]');
    req<bool>(item, 'suspicious_content', '$where.evidence[$ref]');
    refs.add(ref);
  }
  for (final h in req<List<dynamic>>(
    json,
    'hypotheses',
    where,
  ).cast<Map<String, dynamic>>()) {
    final cited = req<List<dynamic>>(
      h,
      'evidence_refs',
      '$where.hypotheses',
    ).cast<String>();
    final missing = cited.where((r) => !refs.contains(r));
    if (missing.isNotEmpty) {
      throw FormatException('$where: hypothesis cites $missing');
    }
    req<num>(h, 'confidence', '$where.hypotheses');
  }
  for (final p in req<List<dynamic>>(
    json,
    'proposals',
    where,
  ).cast<Map<String, dynamic>>()) {
    final at = '$where.proposals';
    req<String>(p, 'action', at);
    req<Map<String, dynamic>>(p, 'params', at);
    req<String>(p, 'risk_tier', at);
    req<String>(p, 'approval_requirement', at);
    req<Map<String, dynamic>>(p, 'dry_run_result', at);
    if (!_sha256.hasMatch(req<String>(p, 'fingerprint', at))) {
      throw FormatException('$at: fingerprint is not SHA-256 hex');
    }
    reqTime(p, 'expires_at', at);
  }
  req<List<dynamic>>(json, 'executions', where);
}

/// Parses one WebSocket message (C3); events keep an unknown `event` as unknown.
WsEventName? parseWsMessage(Map<String, dynamic> json, String where) {
  final type = req<String>(json, 'type', where);
  if (type != 'event') return null;
  req<String>(json, 'event_id', where);
  req<String>(json, 'incident_id', where);
  req<int>(json, 'state_version', where);
  reqTime(json, 'ts', where);
  req<Map<String, dynamic>>(json, 'data', where);
  return WsEventName.parse(req<String>(json, 'event', where));
}

/// Parses a fixture timeline: ordered events, then the final IncidentDetail.
int parseTimeline(Map<String, dynamic> json, String where) {
  final steps = req<List<dynamic>>(
    json,
    'steps',
    where,
  ).cast<Map<String, dynamic>>();
  var lastVersion = 0;
  for (final step in steps) {
    req<int>(step, 'delay_ms', '$where.steps');
    final event = req<Map<String, dynamic>>(step, 'event', '$where.steps');
    final name = parseWsMessage(event, '$where.steps');
    if (name == null || name == WsEventName.unknown) {
      throw FormatException('$where: step is not a known event');
    }
    final version = event['state_version'] as int;
    if (version <= lastVersion) {
      throw FormatException('$where: state_version does not rise');
    }
    lastVersion = version;
  }
  parseIncidentDetail(
    req<Map<String, dynamic>>(json, 'final', where),
    '$where.final',
  );
  return steps.length;
}
