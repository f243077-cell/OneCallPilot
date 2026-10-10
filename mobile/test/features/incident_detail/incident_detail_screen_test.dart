// MOB-004: Incident Detail — ranked hypotheses with confidence bars and
// categories, `E#` chips that scroll to the evidence card, dropped hypotheses
// hidden by default; loading, error and unknown values.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/models/ws_event.dart';
import 'package:oncallpilot/shared/widgets/evidence_card.dart';

import '../../support/fakes.dart';
import '../../support/sample_incident.dart';
import '../../support/screen_harness.dart';

void main() {
  late FakeIncidentRepository repo;
  final now = openedAt.add(const Duration(minutes: 3));

  setUp(() => repo = FakeIncidentRepository());

  Future<void> pump(WidgetTester tester, {bool settle = true}) => pumpScreens(
    tester,
    repository: repo,
    clock: () => now,
    initialLocation: '/incidents/$detailId',
    settle: settle,
  );

  void serve(Map<String, dynamic> json) =>
      repo.onDetail = (_) async => IncidentDetail.fromJson(json);

  testWidgets('shows a spinner while loading', (tester) async {
    repo.onDetail = (_) => Completer<IncidentDetail>().future;
    await pump(tester, settle: false);
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });

  testWidgets('an unknown incident says so; retry refetches', (tester) async {
    repo.onDetail = (_) async =>
        throw const ApiError(ApiErrorCode.notFound, status: 404, message: 'x');
    await pump(tester);
    expect(find.text('Incident not found'), findsOneWidget);
    serve(incidentJson());
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Checkout errors on api'), findsOneWidget);
  });

  testWidgets('header, ranked hypotheses, confidence and categories', (
    tester,
  ) async {
    serve(incidentJson());
    await pump(tester);
    expect(find.text('SEV1'), findsOneWidget);
    expect(find.text('Api · Awaiting approval'), findsOneWidget);
    expect(find.text('3 min ago'), findsOneWidget);
    expect(find.text('#1'), findsOneWidget);
    expect(find.text('#2'), findsOneWidget);
    expect(find.text('Bad deploy'), findsOneWidget);
    expect(find.text('Application bug'), findsOneWidget);
    expect(find.text('86 %'), findsOneWidget);
    expect(find.text('30 %'), findsOneWidget);
    // Rank 1 is above rank 2, although the JSON lists rank 2 first.
    expect(
      tester.getTopLeft(find.text('#1')).dy,
      lessThan(tester.getTopLeft(find.text('#2')).dy),
    );
  });

  testWidgets('dropped hypotheses are hidden until asked for', (tester) async {
    serve(incidentJson());
    await pump(tester);
    expect(find.text('Traffic surge'), findsNothing);
    await tester.ensureVisible(find.text('Show 1 dropped'));
    await tester.tap(find.text('Show 1 dropped'));
    await tester.pumpAndSettle();
    expect(find.text('Traffic surge'), findsOneWidget);
    expect(find.text('Dropped'), findsOneWidget);
    await tester.ensureVisible(find.text('Hide dropped hypotheses'));
    await tester.tap(find.text('Hide dropped hypotheses'));
    await tester.pumpAndSettle();
    expect(find.text('Traffic surge'), findsNothing);
  });

  testWidgets('MOB-004: tapping E3 scrolls EvidenceCard(E3) into view', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1080, 1600);
    tester.view.devicePixelRatio = 2.0; // 540 x 800 logical
    addTearDown(tester.view.reset);
    serve(incidentJson());
    await pump(tester);

    final card = find.byKey(const ValueKey('evidence-E3'), skipOffstage: false);
    final screen = tester.getRect(find.byType(Scaffold).first);
    bool visible() =>
        card.evaluate().isNotEmpty &&
        screen.overlaps(tester.getRect(card)) &&
        tester.getRect(card).top < screen.bottom;
    expect(visible(), isFalse);

    await tester.tap(
      find.byKey(
        const ValueKey('chip-88888888-8888-4888-8888-000000000001-E3'),
      ),
    );
    await tester.pumpAndSettle();
    expect(visible(), isTrue);
    expect(tester.widget<EvidenceCard>(card).evidence.ref, 'E3');
  });

  testWidgets('a pending proposal opens Action Review', (tester) async {
    serve(incidentJson());
    await pump(tester);
    await tester.tap(find.text('Review proposed action'));
    await tester.pumpAndSettle();
    expect(find.text('proposal $pendingId'), findsOneWidget);
  });

  testWidgets('no hypotheses or evidence yet', (tester) async {
    serve(
      incidentJson(
        overrides: {
          'status': 'investigating',
          'pending_proposal_id': null,
          'proposals': <Object>[],
          'hypotheses': <Object>[],
          'evidence': <Object>[],
        },
      ),
    );
    await pump(tester);
    expect(find.text('No hypotheses yet'), findsOneWidget);
    expect(find.text('No evidence yet'), findsOneWidget);
    expect(find.text('Review proposed action'), findsNothing);
  });

  testWidgets('unknown enum values and evidence kinds are shown, not fatal', (
    tester,
  ) async {
    serve(
      incidentJson(
        overrides: {
          'status': 'paused',
          'severity': 'sev9',
          'pending_proposal_id': null,
          'proposals': <Object>[],
          'hypotheses': [
            hypothesisJson(1, 'reconsidered', ['E1'], category: 'cosmic_rays'),
          ],
          'evidence': [
            evidenceJson('E1', 'trace_query', {'spans': 3}),
            evidenceJson('E2', 'metric_query', {'template': 'error_rate'}),
          ],
        },
      ),
    );
    await pump(tester);
    expect(find.text('SEV?'), findsOneWidget);
    expect(find.text('Api · Unknown'), findsOneWidget);
    expect(find.text('Unknown'), findsOneWidget); // the category
    expect(find.text('Unknown evidence'), findsOneWidget);
    // An unknown kind, and a known kind with a broken payload.
    expect(
      find.text('This app version cannot show this evidence type.'),
      findsNWidgets(2),
    );
  });

  testWidgets('a newer event for this incident refetches it', (tester) async {
    serve(incidentJson());
    await pump(tester);
    serve(
      incidentJson(
        overrides: {
          'status': 'executing',
          'state_version': 13,
          'pending_proposal_id': null,
        },
      ),
    );
    repo.eventsController.add(
      WsEvent.fromJson({
        'event_id': '1760000000000-13',
        'incident_id': detailId,
        'state_version': 13,
        'ts': now.toIso8601String(),
        'event': 'incident.updated',
        'data': {
          'status': 'executing',
          'status_reason': null,
          'severity': 'sev1',
        },
      }),
    );
    await tester.pumpAndSettle();
    expect(find.text('Api · Executing'), findsOneWidget);
    final calls = repo.detailCalls;
    repo.eventsController.add(
      WsEvent.fromJson({
        'event_id': '1760000000000-14',
        'incident_id': 'another-incident',
        'state_version': 99,
        'ts': now.toIso8601String(),
        'event': 'incident.updated',
        'data': {
          'status': 'executing',
          'status_reason': null,
          'severity': 'sev1',
        },
      }),
    );
    await tester.pumpAndSettle();
    expect(repo.detailCalls, calls);
  });
}
