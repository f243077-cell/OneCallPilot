// MOB-006: Incident Detail polls the log tail every 5 s while it is the
// visible screen, passes the previous `until` as `since`, pauses while another
// screen covers it and stops when it is closed (fake repository call counter).

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/models/evidence_payloads.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/models/ops.dart';

import '../../support/fakes.dart';
import '../../support/sample_incident.dart';
import '../../support/screen_harness.dart';

void main() {
  late FakeIncidentRepository repo;
  final now = openedAt.add(const Duration(minutes: 3));
  late List<DateTime?> sinces;

  setUp(() {
    repo = FakeIncidentRepository();
    sinces = [];
    final json = incidentJson();
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    repo.onList = () async => [IncidentSummary.fromJson(json)];
    var n = 0;
    repo.onLogTail = (_, since) async {
      sinces.add(since);
      n++;
      return LogTail(
        lines: [
          LogLine.fromJson({
            'ts': now.add(Duration(seconds: n)).toIso8601String(),
            'level': 'ERROR',
            'msg': 'checkout failed #$n',
            'exc_type': null,
          }),
        ],
        until: now.add(Duration(seconds: n * 5)),
      );
    };
  });

  testWidgets('polls every 5 s, passing until as since; shows the lines', (
    tester,
  ) async {
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => now,
      initialLocation: '/incidents/$detailId',
    );
    expect(repo.logTailCalls, 1);
    await tester.pump(const Duration(seconds: 5));
    await tester.pump(const Duration(seconds: 5));
    expect(repo.logTailCalls, 3);
    expect(sinces, [
      null,
      now.add(const Duration(seconds: 5)),
      now.add(const Duration(seconds: 10)),
    ]);
    expect(find.textContaining('checkout failed #3'), findsOneWidget);
    expect(find.textContaining('checkout failed #1'), findsOneWidget);
  });

  testWidgets('pauses under another screen; stops when closed', (tester) async {
    final router = await pumpScreens(
      tester,
      repository: repo,
      clock: () => now,
      initialLocation: '/incidents',
    );
    await tester.tap(find.text('Checkout errors on api'));
    await tester.pumpAndSettle();
    final opened = repo.logTailCalls;
    expect(opened, greaterThanOrEqualTo(1));

    router.push('/settings'); // covers Incident Detail
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 20));
    expect(repo.logTailCalls, opened, reason: 'hidden: no polling');

    router.pop();
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 5));
    expect(repo.logTailCalls, greaterThan(opened), reason: 'visible again');

    router.pop(); // back to the feed: the screen is gone
    await tester.pumpAndSettle();
    final closed = repo.logTailCalls;
    await tester.pump(const Duration(seconds: 30));
    expect(repo.logTailCalls, closed);
  });

  testWidgets('an error is shown and polling goes on', (tester) async {
    repo.onLogTail = (_, _) async => throw const ApiError(
      ApiErrorCode.rateLimited,
      status: 429,
      message: 'slow down',
      details: {'retry_after': 2},
    );
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => now,
      initialLocation: '/incidents/$detailId',
    );
    expect(find.byKey(const ValueKey('log-tail-error')), findsOneWidget);
    await tester.pump(const Duration(seconds: 5));
    expect(repo.logTailCalls, 2);
  });
}
