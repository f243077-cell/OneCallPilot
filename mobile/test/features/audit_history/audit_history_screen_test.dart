// MOB-012: Audit History from an audit fixture — who, when and why for
// proposals, approvals, rejections, executions and outcomes; agent internals
// hidden; one incident's entries when opened from it, with a way to see all.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:oncallpilot/data/models/ops.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';
import 'package:oncallpilot/features/audit_history/presentation/audit_history_screen.dart';
import 'package:oncallpilot/features/auth/application/auth_session.dart';

import '../../support/fakes.dart';
import '../../support/test_app.dart';

const me = '00000000-0000-4000-8000-000000000001';
const incident = '6f1c2a4e-0b7d-4c1e-9a53-2d8e4f6a7b01';

Map<String, dynamic> entry(
  int id,
  String event,
  String actor,
  Map<String, dynamic> payload, {
  String? incidentId = incident,
}) => {
  'id': id,
  'actor': actor,
  'event': event,
  'ref_type': 'proposal',
  'ref_id': '8a2d3b5f-1c8e-4d2f-8b64-3e9f5a7b8c02',
  'incident_id': incidentId,
  'payload': payload,
  'created_at': DateTime.utc(2026, 10, 14, 9, id).toIso8601String(),
};

/// Newest first, as the API returns them.
final fixture = [
  entry(9, 'incident.resolved', 'system', {'resolution': 'action_succeeded'}),
  entry(8, 'health.verified', 'agent', {'verdict': 'pass'}),
  entry(7, 'execution.finished', 'runner', {'status': 'succeeded'}),
  entry(6, 'approval.approved', 'user:$me', {'auth_method': 'biometric'}),
  entry(5, 'approval.rejected', 'user:11111111-2222-4333-8444-555555555555', {
    'reason': 'wrong release',
  }),
  entry(4, 'llm.call', 'agent', {'tokens': 900}),
  entry(3, 'proposal.created', 'agent', {
    'action': 'rollback_deploy',
    'params': {'service': 'api', 'target_release': '1.4.0'},
  }),
  entry(2, 'evidence.added', 'agent', {'ref': 'E1'}),
  entry(1, 'incident.opened', 'detector', {'title': 'api errors'}),
];

void main() {
  late FakeIncidentRepository repo;
  late List<String?> asked;

  setUp(() {
    repo = FakeIncidentRepository();
    asked = [];
    repo.onAudit = (incidentId) async {
      asked.add(incidentId);
      return fixture.map(AuditEntry.fromJson).toList();
    };
  });

  Future<GoRouter> pump(WidgetTester tester, String location) async {
    final app = await TestApp.create(signedIn: true);
    final router = GoRouter(
      initialLocation: location,
      routes: [
        GoRoute(
          path: '/audit',
          builder: (_, state) => AuditHistoryScreen(
            incidentId: state.uri.queryParameters['incident'],
          ),
        ),
        GoRoute(
          path: '/incidents/:id',
          builder: (_, state) =>
              Scaffold(body: Text('incident ${state.pathParameters['id']}')),
        ),
      ],
    );
    addTearDown(router.dispose);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          incidentRepositoryProvider.overrideWithValue(repo),
          authStateProvider.overrideWithValue(
            app.container.read(authStateProvider),
          ),
        ],
        child: MaterialApp.router(routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
    return router;
  }

  testWidgets('who, when and why, newest first; internals hidden', (
    tester,
  ) async {
    await pump(tester, '/audit');
    expect(asked, [null]);
    final titles = tester
        .widgetList<ListTile>(find.byType(ListTile))
        .map((t) => (t.title! as Text).data)
        .toList();
    expect(titles, [
      'Incident resolved',
      'Recovery checked',
      'Execution finished',
      'Approved',
      'Rejected',
      'Action proposed',
      'Incident opened',
    ]);
    expect(find.text('LLM call'), findsNothing);
    expect(find.textContaining('You · '), findsOneWidget);
    expect(find.textContaining('Confirmed by biometric'), findsOneWidget);
    expect(find.textContaining('A user · '), findsOneWidget);
    expect(find.textContaining('Reason: wrong release'), findsOneWidget);
    expect(
      find.textContaining('rollback deploy service=api, target_release=1.4.0'),
      findsOneWidget,
    );
    expect(find.textContaining('Status: succeeded'), findsOneWidget);
    expect(find.textContaining('Verdict: pass'), findsOneWidget);
    expect(find.textContaining('Runner · '), findsOneWidget);
  });

  testWidgets('an entry opens its incident', (tester) async {
    await pump(tester, '/audit');
    await tester.tap(find.text('Rejected'));
    await tester.pumpAndSettle();
    expect(find.text('incident $incident'), findsOneWidget);
  });

  testWidgets('filtered by incident; "All incidents" lifts the filter', (
    tester,
  ) async {
    await pump(tester, '/audit?incident=$incident');
    expect(asked, [incident]);
    await tester.tap(find.byKey(const ValueKey('all-incidents')));
    await tester.pumpAndSettle();
    expect(asked, [incident, null]);
    expect(find.byKey(const ValueKey('all-incidents')), findsNothing);
  });

  testWidgets('no entries yet', (tester) async {
    repo.onAudit = (_) async => [];
    await pump(tester, '/audit');
    expect(find.text('No entries yet'), findsOneWidget);
  });

  test('actor labels', () {
    expect(actorLabel('user:$me', me: me), 'You');
    expect(actorLabel('user:$me'), 'A user');
    expect(actorLabel('detector'), 'Detector');
  });
}
