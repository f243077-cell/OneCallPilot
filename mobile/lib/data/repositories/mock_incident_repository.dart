// Mock mode (MOB-017, architecture §10.6): replays fixture timelines with
// their delays and answers challenge, approve and reject like the server,
// with the checks and error codes of contracts/approval-protocol.md, all
// offline. Codes that need server state the fixtures lack (RATE_LIMITED,
// COOLDOWN_ACTIVE, INCIDENT_BUSY, INTERNAL, UNAUTHORIZED, …) come from
// [MockIncidentRepository.failNext].

import 'dart:async';
import 'dart:convert';
import 'dart:math';

import '../../core/errors/api_error.dart';
import '../models/approval.dart';
import '../models/enums.dart';
import '../models/incident.dart';
import '../models/ops.dart';
import '../models/ws_event.dart';
import 'fixture_timelines.dart';
import 'incident_repository.dart';

typedef Clock = DateTime Function();
typedef Delay = Future<void> Function(Duration duration);

enum MockOperation { challenge, approve, reject }

const challengeTtl = Duration(seconds: 120);

class MockIncidentRepository implements IncidentRepository {
  MockIncidentRepository(
    List<Map<String, dynamic>> timelines, {
    Clock? clock,
    Delay? delay,
    Random? random,
  }) : _clock = clock ?? DateTime.now,
       _delay = delay ?? Future<void>.delayed,
       _random = random ?? Random.secure() {
    final now = _clock().toUtc();
    _timelines = [
      for (final json in timelines)
        FixtureTimeline.fromJson(json, startAt: now),
    ];
  }

  final Clock _clock;
  final Delay _delay;
  final Random _random;
  late final List<FixtureTimeline> _timelines;

  final _events = StreamController<WsEvent>.broadcast();

  /// Incident state as raw C1 JSON, built from the events emitted so far.
  final _incidents = <String, Map<String, dynamic>>{};
  final _gates = <String, Completer<void>>{};
  final _stopped = <String>{};
  final _decided = <String>{};
  final _challenges = <String, _Challenge>{};
  final _idempotent = <String, _Stored>{};
  final _forced = <MockOperation, List<ApiError>>{};
  final _audit = <Map<String, dynamic>>[];
  Map<String, dynamic> _settings = _defaultSettings();
  bool _disposed = false;

  /// The mock's signed-in user, as the audit log names them.
  static const userActor = 'user:00000000-0000-4000-8000-000000000001';

  static Map<String, dynamic> _defaultSettings() => {
    'services': ['api', 'worker', 'redis'],
    'thresholds': {'error_rate.z': 4.0, 'p95_latency.min_seconds': 0.3},
    'notify': {'min_push_severity': 'sev2'},
    'version': 1,
    'updated_at': DateTime.utc(2026, 10, 1).toIso8601String(),
    'updated_by': null,
    'locked': false,
    'lock_reason': null,
  };

  /// Starts replaying every timeline at once.
  void start() {
    for (final timeline in _timelines) {
      unawaited(_replay(timeline));
    }
  }

  /// Makes the next call of [operation] fail with [code] (MOB-017).
  void failNext(MockOperation operation, ApiErrorCode code, {int? retryAfter}) {
    _forced
        .putIfAbsent(operation, () => [])
        .add(
          ApiError(
            code,
            status: statusFor(code),
            message: 'Simulated ${code.wire}',
            details: {'retry_after': ?retryAfter},
          ),
        );
  }

  /// Locks the settings as BENCHMARK_LOCK does (null unlocks).
  void lockSettings(String? reason) {
    _settings
      ..['locked'] = reason != null
      ..['lock_reason'] = reason;
  }

  static int statusFor(ApiErrorCode code) => switch (code) {
    ApiErrorCode.unauthorized => 401,
    ApiErrorCode.forbidden || ApiErrorCode.biometricRequired => 403,
    ApiErrorCode.notFound => 404,
    ApiErrorCode.idempotencyKeyRequired => 400,
    ApiErrorCode.validationError || ApiErrorCode.idempotencyConflict => 422,
    ApiErrorCode.rateLimited || ApiErrorCode.cooldownActive => 429,
    ApiErrorCode.benchmarkLocked => 423,
    ApiErrorCode.internal || ApiErrorCode.unknown => 500,
    _ => 409,
  };

  // --- replay ---------------------------------------------------------------

  Future<void> _replay(FixtureTimeline timeline) async {
    for (final step in timeline.steps) {
      if (step.awaitApproval) {
        // One gate per approval: a timeline may wait twice (primary, rollback).
        await _gates.putIfAbsent(timeline.incidentId, Completer.new).future;
        _gates.remove(timeline.incidentId);
      }
      await _delay(step.delay);
      if (_disposed || _stopped.contains(timeline.incidentId)) return;
      _apply(timeline, step.event);
    }
    _incidents[timeline.incidentId] = _copy(timeline.finalDetail);
  }

  void _apply(FixtureTimeline timeline, Map<String, dynamic> event) {
    final id = timeline.incidentId;
    final data = event['data'] as Map<String, dynamic>;
    final ts = event['ts'] as String;
    if (event['event'] == 'incident.opened') {
      final fixed = timeline.finalDetail;
      _incidents[id] = {
        ..._copy(data),
        'window_start': fixed['window_start'],
        'window_end': null,
        'trigger': _copy(fixed['trigger']),
        'evidence': <dynamic>[],
        'hypotheses': <dynamic>[],
        'proposals': <dynamic>[],
        'executions': <dynamic>[],
        'agent_meta': _copy(fixed['agent_meta']),
      };
    }
    final incident = _incidents[id]!;
    switch (event['event']) {
      case 'incident.updated':
        incident.addAll(_copy(data));
      case 'evidence.added':
        (incident['evidence'] as List).add(
          _copy(data)..remove('payload_truncated'),
        );
      case 'hypothesis.updated':
        final hypotheses = _copy(data['hypotheses']) as List;
        incident['hypotheses'] = hypotheses;
        final top = hypotheses.cast<Map<String, dynamic>>().where(
          (h) => h['status'] == 'active' && h['rank'] == 1,
        );
        incident['top_hypothesis'] = top.isEmpty
            ? null
            : {
                'summary': top.first['summary'],
                'confidence': top.first['confidence'],
                'root_cause_category': top.first['root_cause_category'],
              };
      case 'proposal.created':
        (incident['proposals'] as List).add(_copy(data));
        if (data['status'] == 'pending') {
          incident['pending_proposal_id'] = data['id'];
        }
      case 'proposal.updated':
        final proposal = _proposalIn(incident, data['proposal_id'] as String)!;
        proposal['status'] = data['status'];
        proposal['status_reason'] = data['status_reason'];
        if (incident['pending_proposal_id'] == data['proposal_id'] &&
            data['status'] != 'pending') {
          incident['pending_proposal_id'] = null;
        }
      case 'execution.progress':
        final executions = (incident['executions'] as List)
            .cast<Map<String, dynamic>>();
        var execution = executions
            .where((e) => e['id'] == data['execution_id'])
            .firstOrNull;
        if (execution == null) {
          execution = {
            'id': data['execution_id'],
            'proposal_id': data['proposal_id'],
            'status': 'running',
            'steps': <dynamic>[],
            'error_code': null,
            'health_after': null,
            'started_at': ts,
            'finished_at': null,
          };
          executions.add(execution);
        }
        (execution['steps'] as List).add({
          'at': ts,
          'step': data['step'],
          'status': data['status'],
          'message': data['message'],
        });
      case 'incident.resolved':
        incident
          ..['status'] = 'resolved'
          ..['resolution'] = data['resolution']
          ..['resolved_at'] = data['resolved_at']
          ..['window_end'] =
              timeline.finalDetail['window_end'] ?? data['resolved_at'];
    }
    incident['state_version'] = event['state_version'];
    _record(id, event['event'] as String, data, ts);
    _events.add(WsEvent.fromJson(event));
  }

  /// The audit rows the server would write for an event (architecture §8).
  void _record(
    String incidentId,
    String event,
    Map<String, dynamic> data,
    String ts,
  ) {
    void add(
      String name,
      String actor,
      Map<String, dynamic> payload, [
      String? refType,
      Object? refId,
    ]) {
      _audit.add({
        'id': _audit.length + 1,
        'actor': actor,
        'event': name,
        'ref_type': refType,
        'ref_id': refId,
        'incident_id': incidentId,
        'payload': payload,
        'created_at': ts,
      });
    }

    switch (event) {
      case 'incident.opened':
        add('incident.opened', 'detector', {'title': data['title']});
      case 'proposal.created':
        add(
          'proposal.created',
          'agent',
          {
            'action': data['action'],
            'params': data['params'],
            'kind': data['kind'],
          },
          'proposal',
          data['id'],
        );
      case 'proposal.updated':
        final status = data['status'];
        final ref = data['proposal_id'];
        switch (status) {
          case 'approved':
            add('approval.approved', userActor, {}, 'proposal', ref);
          case 'rejected':
            add(
              'approval.rejected',
              userActor,
              {'reason': data['status_reason']},
              'proposal',
              ref,
            );
          case 'expired':
            add('proposal.expired', 'system', {}, 'proposal', ref);
          case 'succeeded' || 'failed':
            add(
              'execution.finished',
              'runner',
              {'status': status},
              'proposal',
              ref,
            );
        }
      case 'incident.resolved':
        add('incident.resolved', 'system', {'resolution': data['resolution']});
      case 'incident.updated' when data['status'] == 'escalated':
        add('incident.status_changed', 'system', {
          'status': 'escalated',
          'status_reason': data['status_reason'],
        });
    }
  }

  // --- reads ------------------------------------------------------------------

  @override
  Future<List<IncidentSummary>> listIncidents() async {
    final items = _incidents.values.map(IncidentSummary.fromJson).toList()
      ..sort((a, b) => b.openedAt.compareTo(a.openedAt));
    return items;
  }

  @override
  Future<IncidentDetail> getIncident(String id) async {
    final incident = _incidents[id];
    if (incident == null) {
      throw _error(ApiErrorCode.notFound, 'No such incident');
    }
    return IncidentDetail.fromJson(_copy(incident) as Map<String, dynamic>);
  }

  @override
  Stream<WsEvent> events() => _events.stream;

  @override
  Future<LogTail> logTail(String incidentId, {DateTime? since}) async {
    final incident = _incidents[incidentId];
    if (incident == null) {
      throw _error(ApiErrorCode.notFound, 'No such incident');
    }
    final now = _now();
    final after = since ?? now.subtract(const Duration(seconds: 60));
    final lines = [
      for (final e
          in (incident['evidence'] as List).cast<Map<String, dynamic>>())
        if (e['kind'] == 'log_query')
          for (final line
              in ((e['payload'] as Map)['lines'] as List)
                  .cast<Map<String, dynamic>>())
            if (DateTime.parse(line['ts'] as String).isAfter(after)) line,
    ]..sort((a, b) => (a['ts'] as String).compareTo(b['ts'] as String));
    return LogTail.fromJson({
      'lines': lines.take(50).toList(),
      'until': now.toIso8601String(),
    });
  }

  @override
  Future<List<AuditEntry>> listAudit({String? incidentId}) async => [
    for (final row in _audit.reversed)
      if (incidentId == null || row['incident_id'] == incidentId)
        AuditEntry.fromJson(_copy(row) as Map<String, dynamic>),
  ];

  @override
  Future<MonitorSettings> getSettings() async =>
      MonitorSettings.fromJson(_copy(_settings) as Map<String, dynamic>);

  @override
  Future<MonitorSettings> updateSettings(MonitorSettingsUpdate update) async {
    if (_settings['locked'] == true) {
      throw _error(ApiErrorCode.benchmarkLocked, 'Settings are locked');
    }
    if (update.version != _settings['version']) {
      throw _error(ApiErrorCode.versionConflict, 'Settings changed');
    }
    _settings = {
      ..._settings,
      ...update.toJson(),
      'version': update.version + 1,
      'updated_at': _now().toIso8601String(),
      'updated_by': userActor.substring(5),
    };
    _audit.add({
      'id': _audit.length + 1,
      'actor': userActor,
      'event': 'settings.updated',
      'ref_type': null,
      'ref_id': null,
      'incident_id': null,
      'payload': {'version': _settings['version']},
      'created_at': _now().toIso8601String(),
    });
    return getSettings();
  }

  // --- approval protocol (C8) ---------------------------------------------------

  @override
  Future<ChallengeResponse> challenge(
    String proposalId, {
    required String proposalFingerprint,
  }) async {
    _throwForced(MockOperation.challenge);
    final proposal = _findProposal(proposalId);
    _checkPendingAndFresh(proposal);
    if (proposal['fingerprint'] != proposalFingerprint) {
      throw _error(ApiErrorCode.staleProposal, 'Fingerprint does not match');
    }
    final challenge = _Challenge(
      id: _uuid(),
      proposalId: proposalId,
      nonce: _hex(32),
      expiresAt: _now().add(challengeTtl),
    );
    _challenges[challenge.id] = challenge;
    return ChallengeResponse.fromJson({
      'challenge_id': challenge.id,
      'nonce': challenge.nonce,
      'expires_at': challenge.expiresAt.toIso8601String(),
      'approval_requirement': proposal['approval_requirement'],
    });
  }

  @override
  Future<ApproveAccepted> approve(
    String proposalId,
    ApproveRequest body, {
    required String idempotencyKey,
  }) async {
    _throwForced(MockOperation.approve);
    final proposal = _findProposal(proposalId);
    final replay = _idempotency(
      'approve',
      proposalId,
      idempotencyKey,
      body.toJson(),
    );
    if (replay != null) return ApproveAccepted.fromJson(replay);

    final challenge = _challenges[body.challengeId];
    if (challenge == null ||
        challenge.proposalId != proposalId ||
        challenge.nonce != body.nonce ||
        !_now().isBefore(challenge.expiresAt)) {
      throw _error(ApiErrorCode.challengeInvalid, 'Challenge is not valid');
    }
    _challenges.remove(challenge.id); // single use, like GETDEL
    if (proposal['fingerprint'] != body.proposalFingerprint) {
      throw _error(ApiErrorCode.staleProposal, 'Fingerprint does not match');
    }
    _checkPendingAndFresh(proposal);
    if (proposal['risk_tier'] != 'low' &&
        body.authMethod != AuthMethod.biometric) {
      throw _error(
        ApiErrorCode.biometricRequired,
        'Biometric approval required',
      );
    }

    _decided.add(proposalId);
    final executionId = _executionFor(proposalId) ?? _uuid();
    final response = {'execution_id': executionId, 'status': 'queued'};
    _idempotent[idempotencyKey]!.response = response;
    _gates
        .putIfAbsent(proposal['incident_id'] as String, Completer.new)
        .complete();
    return ApproveAccepted.fromJson(response);
  }

  @override
  Future<RejectResponse> reject(
    String proposalId, {
    required String reason,
    required String idempotencyKey,
  }) async {
    _throwForced(MockOperation.reject);
    final proposal = _findProposal(proposalId);
    final replay = _idempotency('reject', proposalId, idempotencyKey, {
      'reason': reason,
    });
    if (replay != null) return RejectResponse.fromJson(replay);
    if (reason.trim().length < 3 || reason.length > 500) {
      throw _error(
        ApiErrorCode.validationError,
        'Reason must be 3–500 characters',
      );
    }
    if (proposal['status'] != 'pending' || _decided.contains(proposalId)) {
      throw _error(ApiErrorCode.proposalNotPending, 'Already decided');
    }
    _decided.add(proposalId);
    final incidentId = proposal['incident_id'] as String;
    _stopped.add(incidentId); // the scripted approval will never come
    _emitDecision(incidentId, proposalId, reason);
    final response = {'proposal_id': proposalId, 'status': 'rejected'};
    _idempotent[idempotencyKey]!.response = response;
    return RejectResponse.fromJson(response);
  }

  /// The events a rejection causes: the proposal is rejected and the incident
  /// escalates with `proposal_rejected` (architecture §5.7).
  void _emitDecision(String incidentId, String proposalId, String reason) {
    final incident = _incidents[incidentId]!;
    final timeline = _timelines.firstWhere((t) => t.incidentId == incidentId);
    var version = incident['state_version'] as int;
    String nextId() => '${_now().millisecondsSinceEpoch}-${version + 1}';
    Map<String, dynamic> event(String name, Map<String, dynamic> data) => {
      'type': 'event',
      'event_id': nextId(),
      'incident_id': incidentId,
      'state_version': ++version,
      'ts': _now().toIso8601String(),
      'event': name,
      'data': data,
    };
    _apply(
      timeline,
      event('proposal.updated', {
        'proposal_id': proposalId,
        'status': 'rejected',
        'status_reason': reason,
        'superseded_by': null,
      }),
    );
    _apply(
      timeline,
      event('incident.updated', {
        'status': 'escalated',
        'status_reason': 'proposal_rejected',
        'severity': incident['severity'],
      }),
    );
  }

  // --- helpers ------------------------------------------------------------------

  DateTime _now() => _clock().toUtc();

  void _throwForced(MockOperation operation) {
    final queue = _forced[operation];
    if (queue != null && queue.isNotEmpty) throw queue.removeAt(0);
  }

  ApiError _error(ApiErrorCode code, String message) =>
      ApiError(code, status: statusFor(code), message: message);

  Map<String, dynamic> _findProposal(String proposalId) {
    for (final incident in _incidents.values) {
      final proposal = _proposalIn(incident, proposalId);
      if (proposal != null) return proposal;
    }
    throw _error(ApiErrorCode.notFound, 'No such proposal');
  }

  static Map<String, dynamic>? _proposalIn(
    Map<String, dynamic> incident,
    String proposalId,
  ) => (incident['proposals'] as List)
      .cast<Map<String, dynamic>>()
      .where((p) => p['id'] == proposalId)
      .firstOrNull;

  void _checkPendingAndFresh(Map<String, dynamic> proposal) {
    if (proposal['status'] != 'pending' || _decided.contains(proposal['id'])) {
      throw _error(ApiErrorCode.proposalNotPending, 'Already decided');
    }
    if (!_now().isBefore(DateTime.parse(proposal['expires_at'] as String))) {
      throw _error(ApiErrorCode.proposalExpired, 'This proposal expired');
    }
  }

  /// Idempotency-Key rules: a missing key is refused; a known key with the
  /// same request replays the stored response; with another request it is
  /// IDEMPOTENCY_CONFLICT. Returns the stored response to replay, if any.
  Map<String, dynamic>? _idempotency(
    String operation,
    String proposalId,
    String key,
    Map<String, dynamic> body,
  ) {
    if (key.isEmpty) {
      throw _error(
        ApiErrorCode.idempotencyKeyRequired,
        'Idempotency-Key header is required',
      );
    }
    final request = jsonEncode([operation, proposalId, body]);
    final stored = _idempotent[key];
    if (stored != null && stored.response != null) {
      if (stored.request != request) {
        throw _error(
          ApiErrorCode.idempotencyConflict,
          'Key reused with another request',
        );
      }
      return stored.response;
    }
    _idempotent[key] = _Stored(request);
    return null;
  }

  String? _executionFor(String proposalId) {
    for (final timeline in _timelines) {
      for (final execution
          in (timeline.finalDetail['executions'] as List)
              .cast<Map<String, dynamic>>()) {
        if (execution['proposal_id'] == proposalId) {
          return execution['id'] as String;
        }
      }
    }
    return null;
  }

  String _hex(int bytes) => [
    for (var i = 0; i < bytes; i++)
      _random.nextInt(256).toRadixString(16).padLeft(2, '0'),
  ].join();

  String _uuid() {
    final h = _hex(16).split('');
    h[12] = '4';
    h[16] = '89ab'[_random.nextInt(4)];
    final s = h.join();
    return '${s.substring(0, 8)}-${s.substring(8, 12)}-${s.substring(12, 16)}-'
        '${s.substring(16, 20)}-${s.substring(20)}';
  }

  static dynamic _copy(Object? json) => jsonDecode(jsonEncode(json));

  @override
  void dispose() {
    _disposed = true;
    unawaited(_events.close());
  }
}

class _Challenge {
  _Challenge({
    required this.id,
    required this.proposalId,
    required this.nonce,
    required this.expiresAt,
  });

  final String id;
  final String proposalId;
  final String nonce;
  final DateTime expiresAt;
}

class _Stored {
  _Stored(this.request);

  final String request;
  Map<String, dynamic>? response;
}
