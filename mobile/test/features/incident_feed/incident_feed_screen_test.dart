// MOB-003: the Incident Feed — severity, service, status, live time since
// alert, open and past tabs, and new incidents without a manual refresh.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/models/ws_event.dart';
import 'package:oncallpilot/shared/widgets/time_since.dart';

import '../../support/fakes.dart';
import '../../support/sample_incident.dart';
import '../../support/screen_harness.dart';

IncidentSummary summary(
  String id, {
  String status = 'investigating',
  String severity = 'sev2',
  String service = 'api',
  String title = 'Checkout errors on api',
  int version = 3,
  Map<String, dynamic> extra = const {},
}) => IncidentSummary.fromJson({
  ...incidentJson(),
  'id': id,
  'status': status,
  'severity': severity,
  'service': service,
  'title': title,
  'state_version': version,
  'status_reason': status == 'escalated' ? 'no_catalogue_action_fits' : null,
  'resolved_at': status == 'resolved' ? openedAt.toIso8601String() : null,
  'resolution': status == 'resolved' ? 'action_succeeded' : null,
  'pending_proposal_id': null,
  ...extra,
});

void main() {
  late FakeIncidentRepository repo;
  late DateTime now;

  setUp(() {
    repo = FakeIncidentRepository();
    now = openedAt.add(const Duration(minutes: 12));
  });

  Future<void> pump(WidgetTester tester, {bool settle = true}) =>
      pumpScreens(tester, repository: repo, clock: () => now, settle: settle);

  testWidgets('shows a spinner while loading', (tester) async {
    repo.onList = () => Completer<List<IncidentSummary>>().future;
    await pump(tester, settle: false);
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });

  testWidgets('shows the error with a retry that refetches', (tester) async {
    repo.onList = () async => throw const ApiError(
      ApiErrorCode.internal,
      status: 500,
      message: 'database unavailable',
    );
    await pump(tester);
    expect(find.text('Cannot load incidents'), findsOneWidget);
    expect(find.text('database unavailable'), findsOneWidget);

    repo.onList = () async => [summary('a')];
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Checkout errors on api'), findsOneWidget);
  });

  testWidgets('empty tabs say so', (tester) async {
    await pump(tester);
    expect(find.text('No open incidents'), findsOneWidget);
    await tester.tap(find.text('Past'));
    await tester.pumpAndSettle();
    expect(find.text('No past incidents'), findsOneWidget);
  });

  testWidgets('open and past tabs, with severity, service, status and age', (
    tester,
  ) async {
    repo.onList = () async => [
      summary('a', status: 'awaiting_approval', severity: 'sev1'),
      summary('b', status: 'escalated', title: 'Slow payments'),
      summary('c', status: 'resolved', title: 'Worker memory'),
    ];
    await pump(tester);
    expect(find.text('SEV1'), findsOneWidget);
    expect(find.text('Api · Awaiting approval'), findsOneWidget);
    expect(
      find.text('Api · Escalated: no catalogue action fits'),
      findsOneWidget,
    );
    expect(find.text('Worker memory'), findsNothing);
    expect(find.text('12 min ago'), findsNWidgets(2));

    await tester.tap(find.text('Past'));
    await tester.pumpAndSettle();
    expect(find.text('Worker memory'), findsOneWidget);
    expect(find.text('Api · Resolved: action succeeded'), findsOneWidget);
  });

  testWidgets('time since alert updates while on screen', (tester) async {
    repo.onList = () async => [summary('a')];
    await pump(tester);
    expect(find.text('12 min ago'), findsOneWidget);
    now = now.add(const Duration(minutes: 1));
    await tester.pump(TimeSince.refresh);
    expect(find.text('13 min ago'), findsOneWidget);
  });

  testWidgets('MOB-003: an incident.opened event appears within one frame', (
    tester,
  ) async {
    repo.onList = () async => [summary('a')];
    await pump(tester);
    final opened = summary('new', title: 'Worker restarting', version: 1);
    final calls = repo.listCalls;
    repo.eventsController.add(
      WsEvent.fromJson({
        'event_id': '1760000000000-1',
        'incident_id': 'new',
        'state_version': 1,
        'ts': openedAt.toIso8601String(),
        'event': 'incident.opened',
        'data': {...incidentJson(), ...summaryJson(opened)},
      }),
    );
    await Future<void>.value(); // the stream delivers the event
    await tester.pump(); // one frame
    expect(find.text('Worker restarting'), findsOneWidget);
    expect(repo.listCalls, calls); // taken from the event, no refetch

    // An event for a known incident with an old state_version is ignored.
    repo.eventsController.add(
      WsEvent.fromJson({
        'event_id': '1760000000000-2',
        'incident_id': 'a',
        'state_version': 2,
        'ts': openedAt.toIso8601String(),
        'event': 'incident.updated',
        'data': {
          'status': 'executing',
          'status_reason': null,
          'severity': 'sev2',
        },
      }),
    );
    await tester.pump();
    expect(repo.listCalls, calls);
  });

  testWidgets('a newer event for a listed incident refetches the list', (
    tester,
  ) async {
    repo.onList = () async => [summary('a')];
    await pump(tester);
    repo.onList = () async => [summary('a', status: 'executing', version: 4)];
    repo.eventsController.add(
      WsEvent.fromJson({
        'event_id': '1760000000000-4',
        'incident_id': 'a',
        'state_version': 4,
        'ts': openedAt.toIso8601String(),
        'event': 'incident.updated',
        'data': {
          'status': 'executing',
          'status_reason': null,
          'severity': 'sev2',
        },
      }),
    );
    await tester.pumpAndSettle();
    expect(find.text('Api · Executing'), findsOneWidget);
  });

  testWidgets('unknown enum values read as unknown', (tester) async {
    repo.onList = () async => [
      summary('a', status: 'paused', severity: 'sev9', service: 'queue'),
    ];
    await pump(tester);
    expect(find.text('SEV?'), findsOneWidget);
    expect(find.text('Unknown · Unknown'), findsOneWidget);
  });

  testWidgets('tapping an incident opens its detail', (tester) async {
    repo.onList = () async => [summary(detailId)];
    repo.onDetail = (_) async => IncidentDetail.fromJson(incidentJson());
    await pump(tester);
    await tester.tap(find.text('Checkout errors on api'));
    await tester.pumpAndSettle();
    expect(find.text('Hypotheses'), findsOneWidget);
  });
}

/// The summary fields of [s] as C1 JSON.
Map<String, dynamic> summaryJson(IncidentSummary s) => {
  'id': s.id,
  'service': s.service.name,
  'severity': s.severity.name,
  'status': 'investigating',
  'status_reason': null,
  'title': s.title,
  'opened_at': s.openedAt.toIso8601String(),
  'resolved_at': null,
  'resolution': null,
  'state_version': s.stateVersion,
  'top_hypothesis': null,
  'pending_proposal_id': null,
};
