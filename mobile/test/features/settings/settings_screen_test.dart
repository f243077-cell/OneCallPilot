// MOB-013: Settings shows the monitored services, thresholds and push
// severity, saves them with their version, reloads on a conflict, and in the
// locked state shows read-only fields with the lock reason. The server URL is
// shown in debug live builds only.

import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/config/env.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/models/enums.dart';
import 'package:oncallpilot/data/models/ops.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';
import 'package:oncallpilot/features/auth/application/auth_session.dart';
import 'package:oncallpilot/features/settings/presentation/settings_screen.dart';

import '../../support/fakes.dart';
import '../../support/test_app.dart';

void main() {
  late MockIncidentRepository server;

  setUp(() {
    server = MockIncidentRepository(const [], random: Random(1));
  });

  Future<void> pump(
    WidgetTester tester, {
    IncidentRepository? repo,
    Env env = const Env(dataSource: DataSource.mock),
    bool debugBuild = false,
  }) async {
    final app = await TestApp.create(signedIn: true);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          incidentRepositoryProvider.overrideWithValue(repo ?? server),
          envProvider.overrideWithValue(env),
          authRepositoryProvider.overrideWithValue(app.auth),
        ],
        child: MaterialApp(home: SettingsScreen(debugBuild: debugBuild)),
      ),
    );
    await tester.pumpAndSettle();
  }

  FilterChip chip(WidgetTester tester, ServiceName s) =>
      tester.widget<FilterChip>(find.byKey(ValueKey('service-${s.name}')));

  TextField field(WidgetTester tester, String key) =>
      tester.widget<TextField>(find.byKey(ValueKey('threshold-$key')));

  testWidgets('shows the current settings; edits are saved with the version', (
    tester,
  ) async {
    await pump(tester);
    expect(chip(tester, ServiceName.api).selected, isTrue);
    expect(chip(tester, ServiceName.lb).selected, isFalse);
    expect(field(tester, 'error_rate.z').controller!.text, '4');
    expect(field(tester, 'p95_latency.min_seconds').controller!.text, '0.3');
    expect(find.textContaining('Version 1'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('service-lb')));
    await tester.enterText(
      find.byKey(const ValueKey('threshold-error_rate.z')),
      '5',
    );
    await tester.tap(find.text('SEV1'));
    await tester.pump();
    await tester.ensureVisible(find.byKey(const ValueKey('save-settings')));
    await tester.tap(find.byKey(const ValueKey('save-settings')));
    await tester.pumpAndSettle();

    expect(find.text('Settings saved'), findsOneWidget);
    expect(find.textContaining('Version 2'), findsOneWidget);
    final saved = await server.getSettings();
    expect(saved.services, [
      ServiceName.api,
      ServiceName.worker,
      ServiceName.lb,
      ServiceName.redis,
    ]);
    expect(saved.thresholds['error_rate.z'], 5);
    expect(saved.notify.minPushSeverity, Severity.sev1);
  });

  testWidgets('MOB-013: locked settings are read-only with the reason', (
    tester,
  ) async {
    server.lockSettings('Benchmark run in progress (BENCHMARK_LOCK)');
    await pump(tester);
    expect(find.byKey(const ValueKey('settings-locked')), findsOneWidget);
    expect(
      find.text('Benchmark run in progress (BENCHMARK_LOCK)'),
      findsOneWidget,
    );
    for (final s in monitorable) {
      expect(chip(tester, s).onSelected, isNull);
    }
    expect(field(tester, 'error_rate.z').enabled, isFalse);
    expect(find.byKey(const ValueKey('save-settings')), findsNothing);
    final severity = tester.widget<SegmentedButton<Severity>>(
      find.byKey(const ValueKey('min-severity')),
    );
    expect(severity.onSelectionChanged, isNull);
  });

  testWidgets('a version conflict reloads and says so', (tester) async {
    final fake = FakeIncidentRepository();
    var version = 1;
    fake.onSettings = () async =>
        (await server.getSettings()).copyWithVersion(version);
    fake.onUpdateSettings = (_) async {
      version = 2; // someone else saved first
      throw const ApiError(
        ApiErrorCode.versionConflict,
        status: 409,
        message: 'stale',
      );
    };
    await pump(tester, repo: fake);
    await tester.ensureVisible(find.byKey(const ValueKey('save-settings')));
    await tester.tap(find.byKey(const ValueKey('save-settings')));
    await tester.pumpAndSettle();
    expect(
      find.text(
        'Settings were changed elsewhere — reloaded; check and save again',
      ),
      findsOneWidget,
    );
    expect(find.textContaining('Version 2'), findsOneWidget);
  });

  testWidgets('a threshold that is not a number is refused locally', (
    tester,
  ) async {
    await pump(tester);
    await tester.enterText(
      find.byKey(const ValueKey('threshold-error_rate.z')),
      'four',
    );
    await tester.ensureVisible(find.byKey(const ValueKey('save-settings')));
    await tester.tap(find.byKey(const ValueKey('save-settings')));
    await tester.pumpAndSettle();
    expect(
      find.text('Thresholds must be numbers of 0 or more'),
      findsOneWidget,
    );
    expect((await server.getSettings()).version, 1);
  });

  testWidgets('the server URL shows in debug live builds only', (tester) async {
    const live = Env(
      dataSource: DataSource.live,
      apiBaseUrl: 'http://192.168.1.20:8000',
      wsUrl: 'ws://192.168.1.20:8000/ws/incidents',
    );
    Future<Finder> serverTile() async {
      await tester.drag(find.byType(ListView), const Offset(0, -2000));
      await tester.pumpAndSettle();
      return find.byKey(const ValueKey('server-url'));
    }

    await pump(tester, env: live, debugBuild: true);
    expect(await serverTile(), findsOneWidget);
    expect(find.textContaining('http://192.168.1.20:8000'), findsOneWidget);
    await pump(tester, env: live);
    expect(await serverTile(), findsNothing);
    await pump(tester, debugBuild: true);
    expect(await serverTile(), findsNothing);
  });
}

extension on MonitorSettings {
  MonitorSettings copyWithVersion(int version) => MonitorSettings(
    services: services,
    thresholds: thresholds,
    notify: notify,
    version: version,
    updatedAt: updatedAt,
    updatedBy: updatedBy,
    locked: locked,
    lockReason: lockReason,
  );
}
