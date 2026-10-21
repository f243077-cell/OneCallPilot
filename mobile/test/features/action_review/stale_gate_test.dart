// MOB-015 (A3.4): StaleBanner and the Approve gate for each stale condition —
// the socket not `connectedSynced` (a drop disables Approve within 1 s), an
// expired proposal, and a newer version of the incident announced but not yet
// shown. Reject stays enabled throughout (MOB-010).

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/misc.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/api/ws_client.dart';
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/models/ws_event.dart';
import 'package:oncallpilot/shared/widgets/stale_banner.dart';

import '../../support/fakes.dart';
import '../../support/sample_incident.dart';
import '../../support/screen_harness.dart';

void main() {
  late FakeIncidentRepository repo;
  final now = openedAt.add(const Duration(minutes: 3));

  setUp(() => repo = FakeIncidentRepository());

  ButtonStyleButton button(WidgetTester tester, String key) =>
      tester.widget<ButtonStyleButton>(find.byKey(ValueKey(key)));

  Finder banner(String text) => find.descendant(
    of: find.byKey(const ValueKey('stale-banner')),
    matching: find.text(text),
  );

  Future<void> openReview(
    WidgetTester tester, {
    DateTime? at,
    List<Override> overrides = const [],
  }) async {
    final json = incidentJson();
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => at ?? now,
      initialLocation: '/proposals/$pendingId?incident=$detailId',
      overrides: overrides,
    );
  }

  Future<void> showApprove(WidgetTester tester) => tester.scrollUntilVisible(
    find.byKey(const ValueKey('approve')),
    200,
    scrollable: find.byType(Scrollable).last,
  );

  testWidgets('synced and fresh: no banner, Approve enabled', (tester) async {
    await openReview(tester);
    expect(find.byKey(const ValueKey('stale-banner')), findsNothing);
    await showApprove(tester);
    expect(button(tester, 'approve').onPressed, isNotNull);
  });

  testWidgets('the banner text for each connection status', (tester) async {
    expect(
      StaleBanner.connectionText(ConnectionStatus.connectedSynced),
      isNull,
    );
    expect(
      StaleBanner.connectionText(ConnectionStatus.connecting),
      'Reconnecting — approval is paused',
    );
    expect(
      StaleBanner.connectionText(ConnectionStatus.stale),
      'No updates for 30 s — approval is paused',
    );
    expect(
      StaleBanner.connectionText(ConnectionStatus.offline),
      'Offline — showing the last known state',
    );
  });

  testWidgets('an expired proposal: banner, Approve off, Reject on', (
    tester,
  ) async {
    final expires = DateTime.parse(proposalJson()['expires_at'] as String);
    await openReview(tester, at: expires);
    expect(banner('This proposal expired'), findsOneWidget);
    await showApprove(tester);
    expect(button(tester, 'approve').onPressed, isNull);
    expect(button(tester, 'reject').onPressed, isNotNull);
  });

  testWidgets('a newer version announced: Approve off until it is shown', (
    tester,
  ) async {
    final refetch = Completer<IncidentDetail>();
    await openReview(tester);
    repo.onDetail = (_) => refetch.future;
    repo.eventsController.add(
      WsEvent.fromJson({
        'event_id': '200-0',
        'incident_id': detailId,
        'state_version': 13,
        'ts': now.toIso8601String(),
        'event': 'incident.updated',
        'data': {
          'status': 'awaiting_approval',
          'status_reason': null,
          'severity': 'sev1',
        },
      }),
    );
    await tester.pump();
    await tester.pump();
    expect(banner('This incident changed — updating'), findsOneWidget);
    await showApprove(tester);
    expect(button(tester, 'approve').onPressed, isNull);
    expect(button(tester, 'reject').onPressed, isNotNull);

    refetch.complete(
      IncidentDetail.fromJson(incidentJson(overrides: {'state_version': 13})),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('stale-banner')), findsNothing);
    expect(button(tester, 'approve').onPressed, isNotNull);
  });

  testWidgets('a socket drop disables Approve within 1 s and shows the '
      'banner (the client reports the drop at once: ws_client_test)', (
    tester,
  ) async {
    late _Status status;
    await openReview(
      tester,
      overrides: [
        connectionStatusProvider.overrideWith(() => status = _Status()),
      ],
    );
    await showApprove(tester);
    expect(button(tester, 'approve').onPressed, isNotNull);

    status.set(ConnectionStatus.connecting); // the socket closed
    await tester.pump(const Duration(milliseconds: 500));
    expect(button(tester, 'approve').onPressed, isNull);
    expect(button(tester, 'reject').onPressed, isNotNull);
    expect(banner('Reconnecting — approval is paused'), findsOneWidget);

    status.set(ConnectionStatus.offline);
    await tester.pump();
    expect(banner('Offline — showing the last known state'), findsOneWidget);

    status.set(ConnectionStatus.connectedSynced);
    await tester.pump();
    expect(find.byKey(const ValueKey('stale-banner')), findsNothing);
    expect(button(tester, 'approve').onPressed, isNotNull);
  });

  testWidgets('feed and detail show the banner too', (tester) async {
    late _Status status;
    final json = incidentJson();
    repo.onList = () async => [IncidentSummary.fromJson(json)];
    repo.onDetail = (_) async => IncidentDetail.fromJson(json);
    await pumpScreens(
      tester,
      repository: repo,
      clock: () => now,
      overrides: [
        connectionStatusProvider.overrideWith(() => status = _Status()),
      ],
    );
    expect(find.byKey(const ValueKey('stale-banner')), findsNothing);
    status.set(ConnectionStatus.stale);
    await tester.pump();
    expect(banner('No updates for 30 s — approval is paused'), findsOneWidget);
    await tester.tap(find.text('Checkout errors on api'));
    await tester.pumpAndSettle();
    expect(banner('No updates for 30 s — approval is paused'), findsOneWidget);
  });
}

class _Status extends ConnectionStatusNotifier {
  @override
  ConnectionStatus build() => ConnectionStatus.connectedSynced;

  void set(ConnectionStatus next) => state = next;
}
