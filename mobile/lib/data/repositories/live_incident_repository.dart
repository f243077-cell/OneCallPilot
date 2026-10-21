// The live IncidentRepository (A3.1, architecture §10.1): REST for snapshots
// and the approval protocol (C2, C8), the WebSocket for events (C3).

import '../api/rest_client.dart';
import '../api/ws_client.dart';
import '../models/approval.dart';
import '../models/incident.dart';
import '../models/ws_event.dart';
import 'incident_repository.dart';

class LiveIncidentRepository implements IncidentRepository {
  LiveIncidentRepository(this._rest, this._socket);

  final RestClient _rest;
  final WsClient _socket;

  /// The feed reads at most this many pages of 100 (API-004 pagination).
  static const maxPages = 5;

  @override
  Future<List<IncidentSummary>> listIncidents() async {
    final items = <IncidentSummary>[];
    String? cursor;
    for (var page = 0; page < maxPages; page++) {
      final body = await _rest.get(
        '/incidents',
        query: {'limit': 100, 'cursor': ?cursor},
      );
      items.addAll(
        (body['items'] as List).cast<Map<String, dynamic>>().map(
          IncidentSummary.fromJson,
        ),
      );
      cursor = body['next_cursor'] as String?;
      if (cursor == null) break;
    }
    return items;
  }

  @override
  Future<IncidentDetail> getIncident(String id) async =>
      IncidentDetail.fromJson(await _rest.get('/incidents/$id'));

  @override
  Stream<WsEvent> events() => _socket.events;

  @override
  Future<ChallengeResponse> challenge(
    String proposalId, {
    required String proposalFingerprint,
  }) async => ChallengeResponse.fromJson(
    await _rest.post('/proposals/$proposalId/challenge', {
      'proposal_fingerprint': proposalFingerprint,
    }),
  );

  @override
  Future<ApproveAccepted> approve(
    String proposalId,
    ApproveRequest body, {
    required String idempotencyKey,
  }) async => ApproveAccepted.fromJson(
    await _rest.post(
      '/proposals/$proposalId/approve',
      body.toJson(),
      idempotencyKey: idempotencyKey,
    ),
  );

  @override
  Future<RejectResponse> reject(
    String proposalId, {
    required String reason,
    required String idempotencyKey,
  }) async => RejectResponse.fromJson(
    await _rest.post('/proposals/$proposalId/reject', {
      'reason': reason,
    }, idempotencyKey: idempotencyKey),
  );

  @override
  void dispose() {}
}
