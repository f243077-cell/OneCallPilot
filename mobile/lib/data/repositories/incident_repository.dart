// The app's only door to incident data (architecture §10.1). Screens and
// providers use this interface; `MockIncidentRepository` (fixtures, MOB-017)
// and `LiveIncidentRepository` (REST + WebSocket, task A3.1) implement it.
// Every failure is an ApiError carrying a C2 error code.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/approval.dart';
import '../models/incident.dart';
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

  void dispose();
}

/// Set in `main.dart` for the build's DATA_SOURCE.
final incidentRepositoryProvider = Provider<IncidentRepository>(
  (ref) => throw UnimplementedError(
    'The live incident repository arrives in task A3.1; '
    'run with --dart-define=DATA_SOURCE=mock',
  ),
);
