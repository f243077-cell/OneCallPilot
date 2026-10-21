// MOB-007: Action Review renders every field of a proposal (from the C3
// `proposal.created` fixture); Approve is enabled only for a pending, unexpired
// proposal; Reject asks for a reason (MOB-010); the approval flow in mock mode
// ends on the execution's status screen.

import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/api/ws_client.dart';
import 'package:oncallpilot/data/models/approval.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';
import 'package:oncallpilot/features/action_review/application/biometric_gate.dart';
import 'package:oncallpilot/shared/widgets/evidence_card.dart';

import '../../support/contract_fixtures.dart';
import '../../support/fakes.dart';
import '../../support/sample_incident.dart';
import '../../support/sample_timeline.dart' as timeline;
import '../../support/screen_harness.dart';

final _fixture = File('${contractFixturesDir().path}/ws/proposal.created.json');

class _FixedStatus extends ConnectionStatusNotifier {
  _FixedStatus(this.status);

  final ConnectionStatus status;

  @override
  ConnectionStatus build() => status;
}

class _Gate implements BiometricGate {
  int calls = 0;

  @override
  Future<BiometricOutcome> confirm(String reason) async {
    calls++;
    return BiometricOutcome.confirmed;
  }
}

void main() {
  late FakeIncidentRepository repo;

  setUp(() => repo = FakeIncidentRepository());

  /// Serves an incident holding [proposal] and opens its Action Review.
  Future<void> open(
    WidgetTester tester,
    Map<String, dynamic> proposal, {
    required DateTime now,
  }) async {
    final json = incidentJson(
      overrides: {
        'id': proposal['incident_id'],
        'pending_proposal_id': proposal['status'] == 'pending'
            ? proposal['id']
            : null,
        'proposals': [proposal],
      },
    );
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => now,
      initialLocation:
          '/proposals/${proposal['id']}?incident=${proposal['incident_id']}',
    );
  }

  ButtonStyleButton button(WidgetTester tester, String key) =>
      tester.widget<ButtonStyleButton>(find.byKey(ValueKey(key)));

  Future<void> scrollTo(WidgetTester tester, Finder finder) =>
      tester.scrollUntilVisible(
        finder,
        200,
        scrollable: find.byType(Scrollable).first,
      );

  testWidgets('MOB-007: every field of the proposal.created fixture', (
    tester,
  ) async {
    final data = readJsonObject(_fixture)['data'] as Map<String, dynamic>;
    final created = DateTime.parse(data['created_at'] as String);
    await open(tester, data, now: created.add(const Duration(minutes: 5)));

    expect(find.text('Rollback deploy'), findsOneWidget);
    expect(find.text('Medium risk'), findsOneWidget);
    expect(find.text('Biometric approval'), findsOneWidget);
    expect(find.text('Expires in 10:00'), findsOneWidget);
    expect(find.text('service'), findsOneWidget);
    expect(find.text('target_release'), findsOneWidget);
    expect(find.text('1.4.0'), findsOneWidget);
    expect(find.text(data['expected_effect'] as String), findsOneWidget);
    expect(find.text(data['rationale'] as String), findsOneWidget);
    for (final ref in ['E2', 'E3', 'E4']) {
      expect(find.byKey(ValueKey('evidence-chip-$ref')), findsOneWidget);
    }
    final dryRun = data['dry_run_result'] as Map<String, dynamic>;
    await scrollTo(tester, find.byKey(const ValueKey('rollback-step')));
    expect(find.text(dryRun['predicted_effect'] as String), findsOneWidget);
    expect(
      find.text('cs-api-140-1'),
      findsNWidgets(2),
    ); // would start; exited now
    expect(find.text('start'), findsOneWidget);
    expect(find.text('stop'), findsOneWidget);
    expect(find.text('Active release'), findsOneWidget);
    expect(find.text('1.5.0'), findsOneWidget);
    expect(find.text('Running · Healthy'), findsOneWidget);
    expect(find.text('Exited · None'), findsOneWidget);
    expect(find.text('Rate limit'), findsOneWidget);
    expect(find.text('Cooldown'), findsOneWidget);
    expect(find.text('Slots available'), findsOneWidget);
    expect(
      find.text('Rollback deploy service=api, target_release=1.5.0'),
      findsOneWidget,
    );
    expect(button(tester, 'approve').onPressed, isNotNull);
    expect(button(tester, 'reject').onPressed, isNotNull);
  });

  testWidgets('warnings, unmet preconditions and "Not reversible"', (
    tester,
  ) async {
    final proposal = proposalJson();
    final dryRun = proposal['dry_run_result'] as Map<String, dynamic>;
    dryRun['warnings'] = ['only one replica would serve traffic'];
    (dryRun['preconditions'] as Map<String, dynamic>)['slots_available'] =
        false;
    await open(tester, proposal, now: openedAt.add(const Duration(minutes: 3)));
    await scrollTo(tester, find.byKey(const ValueKey('rollback-step')));
    expect(find.text('only one replica would serve traffic'), findsOneWidget);
    expect(find.text('Slots available: not met'), findsOneWidget);
    expect(find.text('Not reversible'), findsOneWidget);
  });

  testWidgets('an E# chip opens its evidence card', (tester) async {
    await open(
      tester,
      proposalJson(),
      now: openedAt.add(const Duration(minutes: 3)),
    );
    await tester.tap(find.byKey(const ValueKey('evidence-chip-E4')));
    await tester.pumpAndSettle();
    expect(
      find.descendant(
        of: find.byType(BottomSheet),
        matching: find.byType(EvidenceCard),
      ),
      findsOneWidget,
    );
  });

  testWidgets('an expired proposal: Approve disabled, Reject enabled', (
    tester,
  ) async {
    final proposal = proposalJson();
    final expires = DateTime.parse(proposal['expires_at'] as String);
    await open(tester, proposal, now: expires.add(const Duration(seconds: 1)));
    expect(find.text('Expired'), findsOneWidget);
    await scrollTo(tester, find.byKey(const ValueKey('approve')));
    expect(button(tester, 'approve').onPressed, isNull);
    expect(button(tester, 'reject').onPressed, isNotNull);
  });

  testWidgets('Approve turns off the moment the proposal expires', (
    tester,
  ) async {
    final proposal = proposalJson();
    final expires = DateTime.parse(proposal['expires_at'] as String);
    var now = expires.subtract(const Duration(seconds: 2));
    final json = incidentJson(
      overrides: {
        'proposals': [proposal],
      },
    );
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => now,
      initialLocation: '/proposals/$pendingId?incident=$detailId',
    );
    await scrollTo(tester, find.byKey(const ValueKey('approve')));
    expect(button(tester, 'approve').onPressed, isNotNull);
    now = expires;
    await tester.pump(const Duration(seconds: 2));
    expect(button(tester, 'approve').onPressed, isNull);
  });

  for (final status in [
    ConnectionStatus.connecting,
    ConnectionStatus.stale,
    ConnectionStatus.offline,
  ]) {
    testWidgets('MOB-015: ${status.name}: Approve disabled, Reject enabled', (
      tester,
    ) async {
      final json = incidentJson();
      repo.onDetail = (_) async => IncidentDetail.fromJson(json);
      await pumpScreens(
        tester,
        repository: repo,
        clock: () => openedAt.add(const Duration(minutes: 3)),
        initialLocation: '/proposals/$pendingId?incident=$detailId',
        overrides: [
          connectionStatusProvider.overrideWith(() => _FixedStatus(status)),
        ],
      );
      await scrollTo(tester, find.byKey(const ValueKey('approve')));
      expect(button(tester, 'approve').onPressed, isNull);
      expect(button(tester, 'reject').onPressed, isNotNull);
    });
  }

  testWidgets('a decided proposal shows its status; both buttons off', (
    tester,
  ) async {
    await open(tester, {
      ...proposalJson(),
      'status': 'rejected',
      'status_reason': 'not now',
    }, now: openedAt.add(const Duration(minutes: 3)));
    expect(find.text('Rejected'), findsOneWidget);
    expect(find.text('Expires in'), findsNothing);
    await scrollTo(tester, find.byKey(const ValueKey('approve')));
    expect(button(tester, 'approve').onPressed, isNull);
    expect(button(tester, 'reject').onPressed, isNull);
  });

  testWidgets('an unknown proposal says so', (tester) async {
    await open(
      tester,
      proposalJson(),
      now: openedAt.add(const Duration(minutes: 3)),
    );
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => openedAt,
      initialLocation: '/proposals/nope?incident=$detailId',
    );
    expect(find.text('Proposal not found'), findsOneWidget);
  });

  testWidgets('without ?incident= the proposal is found from the feed', (
    tester,
  ) async {
    final json = incidentJson();
    repo.onList = () async => [IncidentSummary.fromJson(json)];
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => openedAt,
      initialLocation: '/proposals/$pendingId',
    );
    expect(find.text('Rollback deploy'), findsOneWidget);
  });

  testWidgets('MOB-010: Reject needs 3–500 characters, then sends them', (
    tester,
  ) async {
    repo.onReject = (id, reason, key) async =>
        RejectResponse(proposalId: id, status: 'rejected');
    await open(
      tester,
      proposalJson(),
      now: openedAt.add(const Duration(minutes: 3)),
    );
    await scrollTo(tester, find.byKey(const ValueKey('reject')));
    await tester.tap(find.byKey(const ValueKey('reject')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const ValueKey('reject-reason')), ' no ');
    await tester.pump();
    expect(button(tester, 'confirm-reject').onPressed, isNull);
    await tester.enterText(
      find.byKey(const ValueKey('reject-reason')),
      'wrong release',
    );
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('confirm-reject')));
    await tester.pumpAndSettle();
    expect(repo.rejects.single.$1, 'wrong release');
  });

  testWidgets('an error is shown; a network error offers Retry', (
    tester,
  ) async {
    repo.onChallenge = (_, _) async => throw const ApiError(
      ApiErrorCode.staleProposal,
      status: 409,
      message: 'x',
    );
    await open(
      tester,
      proposalJson(),
      now: openedAt.add(const Duration(minutes: 3)),
    );
    await scrollTo(tester, find.byKey(const ValueKey('approve')));
    await tester.tap(find.byKey(const ValueKey('approve')));
    await tester.pumpAndSettle();
    expect(find.text('Proposal changed — review again'), findsOneWidget);
    expect(find.byKey(const ValueKey('retry')), findsNothing);

    repo.onReject = (_, _, _) async => throw const NetworkError();
    await scrollTo(tester, find.byKey(const ValueKey('reject')));
    await tester.tap(find.byKey(const ValueKey('reject')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('reject-reason')),
      'later',
    );
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('confirm-reject')));
    await tester.pumpAndSettle();
    expect(
      find.text('No connection — check the network and try again'),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('retry')), findsOneWidget);
  });

  group('mock mode, end to end', () {
    Future<void> approveIn(
      WidgetTester tester, {
      required String riskTier,
      required _Gate gate,
    }) async {
      final server = MockIncidentRepository(
        [timeline.sampleTimeline(riskTier: riskTier)],
        delay: (_) async {},
        random: Random(3),
      )..start();
      addTearDown(server.dispose);
      await tester.runAsync(settle);
      final router = await pumpScreens(
        tester,
        repository: server,
        clock: DateTime.now,
        initialLocation:
            '/proposals/${timeline.proposalId}?incident=${timeline.incidentId}',
        overrides: [biometricGateProvider.overrideWithValue(gate)],
      );
      await scrollTo(tester, find.byKey(const ValueKey('approve')));
      await tester.tap(find.byKey(const ValueKey('approve')));
      await tester.pumpAndSettle();
      if (riskTier == 'low') {
        expect(find.text('Approve this action?'), findsOneWidget);
        await tester.tap(find.byKey(const ValueKey('confirm-approve')));
        await tester.pumpAndSettle();
      }
      expect(
        router.state.uri.toString(),
        '/executions/${timeline.executionId}?incident=${timeline.incidentId}',
      );
      expect(find.text('Execution'), findsOneWidget);
    }

    testWidgets('a low-risk proposal: tap, confirm, execution status', (
      tester,
    ) async {
      final gate = _Gate();
      await approveIn(tester, riskTier: 'low', gate: gate);
      expect(gate.calls, 0);
    });

    testWidgets('a medium-risk proposal: biometric, execution status', (
      tester,
    ) async {
      final gate = _Gate();
      await approveIn(tester, riskTier: 'medium', gate: gate);
      expect(gate.calls, 1);
    });
  });
}
