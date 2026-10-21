// TEST-011: loading, error and retry states of the screens added in Phase 2
// and 3 (Action Review, Execution Status, Audit History, Settings), and the
// execution states not covered elsewhere.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/config/env.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/models/ops.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';
import 'package:oncallpilot/features/audit_history/presentation/audit_history_screen.dart';
import 'package:oncallpilot/features/auth/application/auth_session.dart';
import 'package:oncallpilot/features/settings/presentation/settings_screen.dart';

import '../support/fakes.dart';
import '../support/sample_incident.dart';
import '../support/screen_harness.dart';
import '../support/test_app.dart';

const executionId = '88888888-8888-4888-8888-888888888888';

/// One screen with the repository, a signed-in mock user and mock env.
Future<void> pumpAlone(
  WidgetTester tester,
  FakeIncidentRepository repo,
  Widget screen,
) async {
  final app = await TestApp.create(signedIn: true);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        incidentRepositoryProvider.overrideWithValue(repo),
        authRepositoryProvider.overrideWithValue(app.auth),
        envProvider.overrideWithValue(const Env(dataSource: DataSource.mock)),
      ],
      child: MaterialApp(home: screen),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  late FakeIncidentRepository repo;
  final now = openedAt.add(const Duration(minutes: 3));

  setUp(() => repo = FakeIncidentRepository());

  Future<void> open(
    WidgetTester tester,
    String location, {
    bool settle = true,
  }) => pumpScreens(
    tester,
    repository: repo,
    clock: () => now,
    initialLocation: location,
    settle: settle,
  );

  const boom = ApiError(ApiErrorCode.internal, status: 500, message: 'boom');

  for (final location in [
    '/proposals/$pendingId?incident=$detailId',
    '/executions/$executionId?incident=$detailId',
  ]) {
    testWidgets('$location: spinner while loading, then the error', (
      tester,
    ) async {
      final pending = Completer<IncidentDetail>();
      repo.onDetail = (_) => pending.future;
      await open(tester, location, settle: false);
      await tester.pump();
      expect(find.byType(CircularProgressIndicator), findsOneWidget);
      pending.completeError(boom);
      await tester.pumpAndSettle();
      expect(find.text('boom'), findsOneWidget);
    });
  }

  testWidgets('an execution link without ?incident= finds its incident', (
    tester,
  ) async {
    final json = incidentJson(
      overrides: {
        'status': 'executing',
        'pending_proposal_id': null,
        'proposals': [
          {...proposalJson(), 'status': 'approved'},
        ],
        'executions': [
          {
            'id': executionId,
            'proposal_id': pendingId,
            'status': 'queued',
            'steps': <Object>[],
            'error_code': null,
            'health_after': null,
            'started_at': null,
            'finished_at': null,
          },
        ],
      },
    );
    repo.onList = () async => [IncidentSummary.fromJson(json)];
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    await open(tester, '/executions/$executionId');
    expect(find.text('Queued for the runner'), findsOneWidget);
    expect(find.text('No steps yet'), findsOneWidget);
    expect(find.text('Structural (runner): pending'), findsOneWidget);
  });

  testWidgets('an unknown execution says so', (tester) async {
    repo.onList = () async => [];
    await open(tester, '/executions/$executionId');
    expect(find.text('Execution not found'), findsOneWidget);
  });

  testWidgets('Audit History: error with a retry that refetches', (
    tester,
  ) async {
    var calls = 0;
    repo.onAudit = (_) async {
      calls++;
      if (calls == 1) throw const NetworkError();
      return <AuditEntry>[];
    };
    await pumpAlone(tester, repo, const AuditHistoryScreen());
    expect(
      find.text('No connection — check the network and try again'),
      findsOneWidget,
    );
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('No entries yet'), findsOneWidget);
  });

  testWidgets('Settings: error with a retry; sign-out still reachable', (
    tester,
  ) async {
    var calls = 0;
    repo.onSettings = () async {
      calls++;
      throw boom;
    };
    await pumpAlone(tester, repo, const SettingsScreen());
    expect(find.text('boom'), findsOneWidget);
    expect(find.text('Sign out'), findsOneWidget);
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(calls, 2);
  });
}
