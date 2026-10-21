import 'dart:async';
import 'dart:convert';

import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/models/approval.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/models/ws_event.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';

/// Where a dropped call fails: before the server saw it, or after (its answer
/// is lost on the way back).
enum Drop { beforeServer, afterServer }

/// Wraps the mock server: records every approval call as sent, counts detail
/// fetches, and can lose the network for one call.
class RecordingRepository implements IncidentRepository {
  RecordingRepository(this.server);

  final MockIncidentRepository server;
  final challenges = <String>[];
  final approves = <(String key, String body)>[];
  final rejects = <(String key, String reason)>[];
  int detailCalls = 0;
  final _drops = <MockOperation, Drop>{};

  /// Holds the next challenge until the completer completes.
  Completer<void>? holdChallenge;

  void dropNext(MockOperation operation, Drop where) =>
      _drops[operation] = where;

  Future<T> _call<T>(MockOperation operation, Future<T> Function() send) async {
    final drop = _drops.remove(operation);
    if (drop == Drop.beforeServer) throw const NetworkError();
    final result = await send();
    if (drop == Drop.afterServer) throw const NetworkError();
    return result;
  }

  @override
  Future<List<IncidentSummary>> listIncidents() => server.listIncidents();

  @override
  Future<IncidentDetail> getIncident(String id) {
    detailCalls++;
    return server.getIncident(id);
  }

  @override
  Stream<WsEvent> events() => server.events();

  @override
  Future<ChallengeResponse> challenge(
    String proposalId, {
    required String proposalFingerprint,
  }) async {
    challenges.add(proposalFingerprint);
    await holdChallenge?.future;
    return _call(
      MockOperation.challenge,
      () => server.challenge(
        proposalId,
        proposalFingerprint: proposalFingerprint,
      ),
    );
  }

  @override
  Future<ApproveAccepted> approve(
    String proposalId,
    ApproveRequest body, {
    required String idempotencyKey,
  }) {
    approves.add((idempotencyKey, jsonEncode(body.toJson())));
    return _call(
      MockOperation.approve,
      () => server.approve(proposalId, body, idempotencyKey: idempotencyKey),
    );
  }

  @override
  Future<RejectResponse> reject(
    String proposalId, {
    required String reason,
    required String idempotencyKey,
  }) {
    rejects.add((idempotencyKey, reason));
    return _call(
      MockOperation.reject,
      () => server.reject(
        proposalId,
        reason: reason,
        idempotencyKey: idempotencyKey,
      ),
    );
  }

  @override
  void dispose() => server.dispose();
}
