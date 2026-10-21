// A3.1: the live repository and its REST client against a fake HTTP adapter:
// the token goes in the Authorization header (never the URL), approvals carry
// the Idempotency-Key, error envelopes become ApiErrors with their C2 code, no
// answer becomes NetworkError, a 401 refreshes the session once, and the feed
// follows next_cursor. Responses are the contract fixtures (MOB-020).

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/api/rest_client.dart';
import 'package:oncallpilot/data/api/ws_client.dart';
import 'package:oncallpilot/data/models/approval.dart';
import 'package:oncallpilot/data/models/enums.dart';
import 'package:oncallpilot/data/repositories/live_incident_repository.dart';

import '../../support/contract_fixtures.dart'
    show contractFixturesDir, jsonFiles, readJsonObject;

class Sent {
  Sent(this.options, this.body);

  final RequestOptions options;
  final Object? body;

  String get path => options.uri.path;
  String? header(String name) => options.headers[name] as String?;
}

typedef Reply = FutureOr<(int, Object?)> Function(Sent request);

class FakeAdapter implements HttpClientAdapter {
  final sent = <Sent>[];
  final replies = <Reply>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    Object? body;
    if (requestStream != null) {
      final bytes = await requestStream.expand((c) => c).toList();
      body = jsonDecode(utf8.decode(bytes));
    }
    final request = Sent(options, body);
    sent.add(request);
    final (status, json) = await replies.removeAt(0)(request);
    return ResponseBody.fromString(
      jsonEncode(json),
      status,
      headers: {
        'content-type': ['application/json'],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

class FakeTokens implements TokenSource {
  String? token = 'access-1';
  bool refreshOk = true;
  int refreshes = 0;

  @override
  Future<String?> accessToken() async => token;

  @override
  Future<bool> refresh() async {
    refreshes++;
    if (refreshOk) token = 'access-2';
    return refreshOk;
  }

  @override
  Stream<String> get refreshed => const Stream.empty();
}

Map<String, dynamic> envelope(String code, {Map<String, dynamic>? details}) => {
  'error': {'code': code, 'message': code, 'details': details ?? {}},
};

Map<String, dynamic> fixture(String path) =>
    readJsonObject(File('${contractFixturesDir().path}/$path'));

void main() {
  late FakeAdapter adapter;
  late FakeTokens tokens;
  late LiveIncidentRepository repo;
  late WsClient socket;

  setUp(() {
    adapter = FakeAdapter();
    tokens = FakeTokens();
    final dio = Dio()..httpClientAdapter = adapter;
    socket = WsClient(
      url: Uri.parse('ws://example.test/ws/incidents'),
      connect: (_) => throw UnimplementedError(),
      tokens: tokens,
      refetch: () async {},
    );
    repo = LiveIncidentRepository(
      RestClient('http://192.168.1.20:8000', tokens, dio: dio),
      socket,
    );
  });

  final timeline = fixture('timelines/bad_deploy_success.json');
  final detail = timeline['final'] as Map<String, dynamic>;
  final summary = {
    for (final key in [
      'id',
      'service',
      'severity',
      'status',
      'status_reason',
      'title',
      'opened_at',
      'resolved_at',
      'resolution',
      'state_version',
      'top_hypothesis',
      'pending_proposal_id',
    ])
      key: detail[key],
  };

  test('the token goes in the Authorization header, never the URL', () async {
    adapter.replies.add((_) => (200, detail));
    final incident = await repo.getIncident(detail['id'] as String);
    expect(incident.id, detail['id']);
    final request = adapter.sent.single;
    expect(request.path, '/incidents/${detail['id']}');
    expect(request.header('Authorization'), 'Bearer access-1');
    expect(request.options.uri.hasQuery, isFalse);
    expect(request.options.uri.toString(), isNot(contains('access-1')));
  });

  test('MOB-020: every fixture timeline\'s incident parses', () async {
    final dir = Directory('${contractFixturesDir().path}/timelines');
    for (final file in jsonFiles(dir)) {
      final json = readJsonObject(file)['final'] as Map<String, dynamic>;
      adapter.replies.add((_) => (200, json));
      final incident = await repo.getIncident(json['id'] as String);
      expect(incident.executions.length, (json['executions'] as List).length);
    }
  });

  test('the feed follows next_cursor', () async {
    adapter.replies
      ..add(
        (_) => (
          200,
          {
            'items': [summary],
            'next_cursor': 'c1',
          },
        ),
      )
      ..add(
        (_) => (
          200,
          {
            'items': [summary],
            'next_cursor': null,
          },
        ),
      );
    final items = await repo.listIncidents();
    expect(items, hasLength(2));
    expect(adapter.sent[0].options.queryParameters, {'limit': 100});
    expect(adapter.sent[1].options.queryParameters, {
      'limit': 100,
      'cursor': 'c1',
    });
  });

  test('the feed stops after five pages', () async {
    for (var i = 0; i < 6; i++) {
      adapter.replies.add(
        (_) => (
          200,
          {
            'items': [summary],
            'next_cursor': 'c$i',
          },
        ),
      );
    }
    expect(await repo.listIncidents(), hasLength(5));
  });

  test('challenge, approve and reject send C8 bodies and the key', () async {
    final proposal = fixture('ws/proposal.created.json')['data'] as Map;
    adapter.replies
      ..add(
        (_) => (
          200,
          {
            'challenge_id': '0b9c0a8e-1d2f-4a3b-9c4d-5e6f7a8b9c0d',
            'nonce': 'ab' * 32,
            'expires_at': '2026-10-14T09:03:05Z',
            'approval_requirement': 'biometric',
          },
        ),
      )
      ..add(
        (_) => (
          202,
          {
            'execution_id': '1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f',
            'status': 'queued',
          },
        ),
      )
      ..add(
        (_) => (200, {'proposal_id': proposal['id'], 'status': 'rejected'}),
      );
    final id = proposal['id'] as String;
    final challenge = await repo.challenge(
      id,
      proposalFingerprint: proposal['fingerprint'] as String,
    );
    expect(challenge.approvalRequirement, ApprovalRequirement.biometric);
    final accepted = await repo.approve(
      id,
      ApproveRequest(
        challengeId: challenge.challengeId,
        nonce: challenge.nonce,
        proposalFingerprint: proposal['fingerprint'] as String,
        authMethod: AuthMethod.biometric,
        deviceId: null,
      ),
      idempotencyKey: 'key-1',
    );
    expect(accepted.status, 'queued');
    await repo.reject(id, reason: 'wrong release', idempotencyKey: 'key-2');

    final [challengeCall, approveCall, rejectCall] = adapter.sent;
    expect(challengeCall.path, '/proposals/$id/challenge');
    expect(challengeCall.body, {
      'proposal_fingerprint': proposal['fingerprint'],
    });
    expect(challengeCall.header('Idempotency-Key'), isNull);
    expect(approveCall.path, '/proposals/$id/approve');
    expect(approveCall.header('Idempotency-Key'), 'key-1');
    expect(approveCall.body, {
      'challenge_id': challenge.challengeId,
      'nonce': challenge.nonce,
      'proposal_fingerprint': proposal['fingerprint'],
      'auth_method': 'biometric',
      'device_id': null,
    });
    expect(rejectCall.path, '/proposals/$id/reject');
    expect(rejectCall.header('Idempotency-Key'), 'key-2');
    expect(rejectCall.body, {'reason': 'wrong release'});
  });

  test('error envelopes become ApiErrors with their code', () async {
    for (final (status, code, expected) in [
      (409, 'STALE_PROPOSAL', ApiErrorCode.staleProposal),
      (403, 'BIOMETRIC_REQUIRED', ApiErrorCode.biometricRequired),
      (404, 'NOT_FOUND', ApiErrorCode.notFound),
      (409, 'SOMETHING_NEW', ApiErrorCode.unknown),
    ]) {
      adapter.replies.add((_) => (status, envelope(code)));
      await expectLater(
        repo.getIncident('x'),
        throwsA(
          isA<ApiError>()
              .having((e) => e.code, 'code', expected)
              .having((e) => e.status, 'status', status),
        ),
      );
    }
    adapter.replies.add(
      (_) => (429, envelope('RATE_LIMITED', details: {'retry_after': 42})),
    );
    await expectLater(
      repo.getIncident('x'),
      throwsA(isA<ApiError>().having((e) => e.retryAfter, 'retryAfter', 42)),
    );
  });

  test('a body that is not an envelope is INTERNAL', () async {
    adapter.replies.add((_) => (502, {'detail': 'bad gateway'}));
    await expectLater(
      repo.getIncident('x'),
      throwsA(
        isA<ApiError>().having((e) => e.code, 'code', ApiErrorCode.internal),
      ),
    );
  });

  test(
    'no answer is a NetworkError (safe to retry with the same key)',
    () async {
      adapter.replies.add(
        (r) => throw DioException.connectionError(
          requestOptions: r.options,
          reason: 'refused',
        ),
      );
      await expectLater(repo.getIncident('x'), throwsA(isA<NetworkError>()));
    },
  );

  test(
    'a 401 refreshes the session and retries once with the new token',
    () async {
      adapter.replies
        ..add((_) => (401, envelope('UNAUTHORIZED')))
        ..add((_) => (200, detail));
      await repo.getIncident(detail['id'] as String);
      expect(tokens.refreshes, 1);
      expect(adapter.sent.map((s) => s.header('Authorization')), [
        'Bearer access-1',
        'Bearer access-2',
      ]);
    },
  );

  test('a 401 with a failed refresh is UNAUTHORIZED, without a loop', () async {
    tokens.refreshOk = false;
    adapter.replies.add((_) => (401, envelope('UNAUTHORIZED')));
    await expectLater(
      repo.getIncident('x'),
      throwsA(
        isA<ApiError>().having(
          (e) => e.code,
          'code',
          ApiErrorCode.unauthorized,
        ),
      ),
    );
    expect(adapter.sent, hasLength(1));
  });

  test('signed out: no request at all', () async {
    tokens.token = null;
    await expectLater(repo.listIncidents(), throwsA(isA<ApiError>()));
    expect(adapter.sent, isEmpty);
  });

  test('events come from the socket', () {
    expect(repo.events(), socket.events);
  });
}
