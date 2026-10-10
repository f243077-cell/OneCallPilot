import 'dart:async';

import 'package:oncallpilot/data/auth/key_value_store.dart';
import 'package:oncallpilot/data/models/approval.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/models/ws_event.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';

/// An IncidentRepository the test drives: set what the next list or detail
/// call returns (a value, an error, or a future that never completes) and
/// push events.
class FakeIncidentRepository implements IncidentRepository {
  Future<List<IncidentSummary>> Function() onList = () async => [];
  Future<IncidentDetail> Function(String id) onDetail = (id) =>
      Future.error(StateError('no detail for $id'));
  final eventsController = StreamController<WsEvent>.broadcast();
  int listCalls = 0;
  int detailCalls = 0;

  @override
  Future<List<IncidentSummary>> listIncidents() {
    listCalls++;
    return onList();
  }

  @override
  Future<IncidentDetail> getIncident(String id) {
    detailCalls++;
    return onDetail(id);
  }

  @override
  Stream<WsEvent> events() => eventsController.stream;

  @override
  Future<ChallengeResponse> challenge(
    String proposalId, {
    required String proposalFingerprint,
  }) => throw UnimplementedError();

  @override
  Future<ApproveAccepted> approve(
    String proposalId,
    ApproveRequest body, {
    required String idempotencyKey,
  }) => throw UnimplementedError();

  @override
  Future<RejectResponse> reject(
    String proposalId, {
    required String reason,
    required String idempotencyKey,
  }) => throw UnimplementedError();

  @override
  void dispose() {}
}

class InMemoryStore implements KeyValueStore {
  final values = <String, String>{};

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async => values[key] = value;

  @override
  Future<void> delete(String key) async => values.remove(key);
}

/// Lets queued microtasks and zero-length delays run.
Future<void> settle() async {
  for (var i = 0; i < 100; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}
