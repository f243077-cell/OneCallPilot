// The app's only door to incident data (architecture §10.1). Screens and
// providers use this interface; `MockIncidentRepository` (fixtures, MOB-017)
// and `LiveIncidentRepository` (REST + WebSocket) implement it.
// Every failure is an ApiError carrying a C2 error code.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/approval.dart';
import '../models/incident.dart';
import '../models/ops.dart';
import '../models/ws_event.dart';

abstract interface class IncidentRepository {
  /// The feed, newest first (`GET /incidents`).
  Future<List<IncidentSummary>> listIncidents();

  /// One incident (`GET /incidents/{id}`); ApiError NOT_FOUND if unknown.
  Future<IncidentDetail> getIncident(String id);

  /// Every incident event as it happens (C3 `type: event` messages).
  Stream<WsEvent> events();

  /// `POST /proposals/{id}/challenge` (C8 step 1).
  Future<ChallengeResponse> challenge(
    String proposalId, {
    required String proposalFingerprint,
  });

  /// `POST /proposals/{id}/approve` (C8 step 3); one key per attempt.
  Future<ApproveAccepted> approve(
    String proposalId,
    ApproveRequest body, {
    required String idempotencyKey,
  });

  /// `POST /proposals/{id}/reject`; always allowed while pending.
  Future<RejectResponse> reject(
    String proposalId, {
    required String reason,
    required String idempotencyKey,
  });

  /// `GET /incidents/{id}/log-tail` (API-006): lines after [since], or the
  /// last 60 s; poll again with the answer's `until`.
  Future<LogTail> logTail(String incidentId, {DateTime? since});

  /// `GET /audit` (API-012), newest first; one incident's when [incidentId]
  /// is set.
  Future<List<AuditEntry>> listAudit({String? incidentId});

  /// `GET /settings` (API-013).
  Future<MonitorSettings> getSettings();

  /// `PUT /settings`; ApiError VERSION_CONFLICT or BENCHMARK_LOCKED.
  Future<MonitorSettings> updateSettings(MonitorSettingsUpdate update);

  void dispose();
}

/// Set in `main.dart` for the build's DATA_SOURCE.
final incidentRepositoryProvider = Provider<IncidentRepository>(
  (ref) =>
      throw UnimplementedError('incidentRepositoryProvider is set in main'),
);
