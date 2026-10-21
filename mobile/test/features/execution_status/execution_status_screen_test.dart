// MOB-011: Execution Status shows the runner's steps, both health checks and
// the verdict; a rollback proposal brings "Review rollback"; an incident
// escalated as runner_timeout or runner_refused shows the reason and no
// rollback button.

import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/models/approval.dart';
import 'package:oncallpilot/data/models/enums.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';

import '../../support/contract_fixtures.dart' show contractFixturesDir;
import '../../support/fakes.dart';
import '../../support/sample_incident.dart';
import '../../support/screen_harness.dart';

const executionId = '88888888-8888-4888-8888-888888888888';
const rollbackId = '99999999-9999-4999-8999-999999999999';
final now = openedAt.add(const Duration(minutes: 10));

String _t(int seconds) =>
    openedAt.add(Duration(seconds: seconds)).toIso8601String();

Map<String, dynamic> step(String name, String status, int at) => {
  'at': _t(at),
  'step': name,
  'status': status,
  'message': '$name $status',
};

Map<String, dynamic> executionJson({
  String status = 'succeeded',
  Map<String, dynamic>? healthAfter,
  String? errorCode,
}) => {
  'id': executionId,
  'proposal_id': pendingId,
  'status': status,
  'steps': [
    step('validated', 'succeeded', 130),
    step('starting cs-api-140-1', 'succeeded', 140),
  ],
  'error_code': errorCode,
  'health_after': healthAfter,
  'started_at': _t(130),
  'finished_at': status == 'running' ? null : _t(150),
};

final structuralPassed = {
  'passed': true,
  'checked_at': _t(149),
  'containers': [
    {'name': 'cs-api-140-1', 'state': 'running', 'health': 'healthy'},
  ],
  'probes': [
    {
      'url': 'http://cs-lb/healthz',
      'ok': true,
      'status_code': 200,
      'latency_ms': 3.0,
      'error': null,
    },
  ],
};

Map<String, dynamic> rollbackJson() => {
  ...proposalJson(),
  'id': rollbackId,
  'kind': 'rollback',
  'parent_proposal_id': pendingId,
  'params': {'service': 'api', 'target_release': '1.5.0'},
};

void main() {
  late FakeIncidentRepository repo;

  setUp(() => repo = FakeIncidentRepository());

  Future<void> open(WidgetTester tester, Map<String, dynamic> overrides) async {
    final json = incidentJson(overrides: overrides);
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => now,
      initialLocation: '/executions/$executionId?incident=$detailId',
    );
  }

  testWidgets('steps, both checks passed, and the verdict', (tester) async {
    await open(tester, {
      'status': 'resolved',
      'resolution': 'action_succeeded',
      'proposals': [
        {...proposalJson(), 'status': 'succeeded'},
      ],
      'executions': [
        executionJson(
          healthAfter: {
            'structural': structuralPassed,
            'signals': {
              'passed': true,
              'checked_at': _t(220),
              'window_seconds': 60,
              'signals': ['error_rate'],
            },
            'verdict': 'pass',
          },
        ),
      ],
    });
    expect(find.text('Rollback deploy'), findsOneWidget);
    expect(find.text('Recovered: both health checks passed'), findsOneWidget);
    expect(find.text('Incident: resolved (action succeeded)'), findsOneWidget);
    expect(find.text('validated'), findsOneWidget);
    expect(find.text('starting cs-api-140-1'), findsOneWidget);
    expect(find.text('Structural (runner): passed'), findsOneWidget);
    expect(
      find.text(
        'cs-api-140-1: Running · Healthy\nhttp://cs-lb/healthz: ok (200)',
      ),
      findsOneWidget,
    );
    expect(find.text('Signals (worker): passed'), findsOneWidget);
    expect(find.text('Error rate over 60 s'), findsOneWidget);
    expect(find.byKey(const ValueKey('review-rollback')), findsNothing);
  });

  testWidgets('verifying: the signal check is pending', (tester) async {
    await open(tester, {
      'status': 'verifying',
      'executions': [
        executionJson(
          healthAfter: {
            'structural': structuralPassed,
            'signals': null,
            'verdict': null,
          },
        ),
      ],
    });
    expect(find.text('Done; verifying recovery…'), findsOneWidget);
    expect(find.text('Structural (runner): passed'), findsOneWidget);
    expect(find.text('Signals (worker): pending'), findsOneWidget);
  });

  testWidgets('MOB-011: failed health and a rollback proposal: Review '
      'rollback opens it', (tester) async {
    await open(tester, {
      'status': 'awaiting_approval',
      'pending_proposal_id': rollbackId,
      'proposals': [
        {...proposalJson(), 'status': 'failed'},
        rollbackJson(),
      ],
      'executions': [
        executionJson(
          healthAfter: {
            'structural': structuralPassed,
            'signals': {
              'passed': false,
              'checked_at': _t(220),
              'window_seconds': 60,
              'signals': ['error_rate'],
            },
            'verdict': 'fail',
          },
        ),
      ],
    });
    expect(find.text('Done, but the service did not recover'), findsOneWidget);
    expect(find.text('Signals (worker): failed'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('review-rollback')));
    await tester.pumpAndSettle();
    expect(find.text('Action review'), findsOneWidget);
    expect(
      find.text('Rollback of the action that failed verification'),
      findsOneWidget,
    );
  });

  for (final (reason, text) in [
    ('runner_timeout', 'Escalated: the runner did not answer in time'),
    (
      'runner_refused',
      'Escalated: the runner refused the action (STATE_DRIFT)',
    ),
  ]) {
    testWidgets('MOB-011: escalated as $reason: the reason, no rollback', (
      tester,
    ) async {
      await open(tester, {
        'status': 'escalated',
        'status_reason': reason,
        'pending_proposal_id': null,
        'proposals': [
          {...proposalJson(), 'status': 'failed'},
          // Even if one existed, a runner escalation offers no rollback.
          rollbackJson(),
        ],
        'executions': [
          executionJson(
            status: reason == 'runner_timeout' ? 'running' : 'aborted',
            errorCode: reason == 'runner_timeout' ? null : 'STATE_DRIFT',
          ),
        ],
      });
      expect(find.text(text), findsOneWidget);
      expect(find.byKey(const ValueKey('review-rollback')), findsNothing);
      expect(find.text('Structural (runner): pending'), findsOneWidget);
    });
  }

  testWidgets('an execution not in the incident yet waits for the runner', (
    tester,
  ) async {
    await open(tester, {'executions': <Object>[]});
    expect(find.text('Waiting for the runner…'), findsOneWidget);
  });

  testWidgets('Incident Detail lists executions and opens them', (
    tester,
  ) async {
    final json = incidentJson(
      overrides: {
        'status': 'verifying',
        'executions': [executionJson()],
      },
    );
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    final router = await pumpScreens(
      tester,
      repository: repo,
      clock: () => now,
      initialLocation: '/incidents/$detailId',
    );
    await tester.tap(find.byKey(const ValueKey('execution-$executionId')));
    await tester.pumpAndSettle();
    expect(
      router.state.uri.toString(),
      '/executions/$executionId?incident=$detailId',
    );
    expect(find.text('Execution'), findsOneWidget);
  });

  testWidgets('MOB-011: the scale_failed_rollback fixture in mock mode', (
    tester,
  ) async {
    final file = File(
      '${contractFixturesDir().path}/timelines/scale_failed_rollback.json',
    );
    final fixture = jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
    final server = MockIncidentRepository(
      [fixture],
      delay: (_) async {},
      random: Random(5),
    )..start();
    addTearDown(server.dispose);
    final incidentId = fixture['incident_id'] as String;
    late String primaryExecution;
    await tester.runAsync(() async {
      await settle();
      final detail = await server.getIncident(incidentId);
      final primary = detail.proposals.single;
      final challenge = await server.challenge(
        primary.id,
        proposalFingerprint: primary.fingerprint,
      );
      final accepted = await server.approve(
        primary.id,
        ApproveRequest(
          challengeId: challenge.challengeId,
          nonce: challenge.nonce,
          proposalFingerprint: primary.fingerprint,
          authMethod: AuthMethod.tap,
          deviceId: null,
        ),
        idempotencyKey: 'k1',
      );
      primaryExecution = accepted.executionId;
      await settle(); // runs to the rollback proposal's approval gate
    });
    await pumpScreens(
      tester,
      repository: server,
      clock: DateTime.now,
      initialLocation: '/executions/$primaryExecution?incident=$incidentId',
    );
    expect(find.text('Scale service'), findsOneWidget);
    expect(find.text('done'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('review-rollback')));
    await tester.pumpAndSettle();
    expect(
      find.text('Rollback of the action that failed verification'),
      findsOneWidget,
    );
    final detail = await tester.runAsync(() => server.getIncident(incidentId));
    expect(detail!.status, IncidentStatus.awaitingApproval);
  });
}
