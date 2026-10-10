// A1.6 against the real C1 timelines (contracts/fixtures/timelines, or
// $OCP_CONTRACTS_DIR/fixtures): the feed lists them from MockIncidentRepository,
// and every evidence card of every final incident renders its variant, never
// the "cannot show" fallback (MOB-005: rendered from fixtures).

import 'dart:io';
import 'dart:math';

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/models/enums.dart' as dto;
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';
import 'package:oncallpilot/shared/widgets/evidence_card.dart';

import '../support/contract_fixtures.dart';
import '../support/fakes.dart';
import '../support/screen_harness.dart';

void main() {
  final dir = contractFixturesDir();
  final files = jsonFiles(Directory('${dir.path}/timelines'));
  final skip = files.isEmpty
      ? 'no timelines in ${dir.path} (contracts not merged yet)'
      : false;

  testWidgets('the feed lists the fixture incidents', skip: skip != false, (
    tester,
  ) async {
    final repo = MockIncidentRepository(
      [for (final f in files) readJsonObject(f)],
      delay: (_) async {},
      random: Random(5),
    )..start();
    addTearDown(repo.dispose);
    await pumpScreens(tester, repository: repo, clock: DateTime.now);
    // Before any approval, every incident is still open.
    for (final file in files) {
      final title = (readJsonObject(file)['final'] as Map)['title'] as String;
      expect(find.text(title), findsOneWidget);
    }
  });

  for (final file in files) {
    final name = file.uri.pathSegments.last;
    testWidgets('$name: every evidence card renders its variant', (
      tester,
    ) async {
      final json = readJsonObject(file)['final'] as Map<String, dynamic>;
      final incident = IncidentDetail.fromJson(json);
      final repo = FakeIncidentRepository()..onDetail = (_) async => incident;
      await pumpScreens(
        tester,
        repository: repo,
        clock: () => incident.openedAt.add(const Duration(minutes: 5)),
        initialLocation: '/incidents/${incident.id}',
      );
      expect(find.text(incident.title), findsOneWidget);
      final cards = find.byType(EvidenceCard, skipOffstage: false);
      expect(cards, findsNWidgets(incident.evidence.length));
      expect(
        find.text(
          'This app version cannot show this evidence type.',
          skipOffstage: false,
        ),
        findsNothing,
      );
      final charts = incident.evidence
          .where((e) => e.kind == dto.EvidenceKind.metricQuery)
          .length;
      expect(
        find.byType(LineChart, skipOffstage: false),
        findsNWidgets(charts),
      );
      // Every cited E# chip scrolls to its card.
      for (final h in incident.hypotheses) {
        for (final ref in h.evidenceRefs) {
          final chip = find.byKey(ValueKey('chip-${h.id}-$ref'));
          await tester.ensureVisible(chip);
          await tester.tap(chip);
          await tester.pumpAndSettle();
          expect(
            find.byKey(ValueKey('evidence-$ref')).hitTestable(),
            findsOneWidget,
          );
        }
      }
    }, skip: skip != false);
  }
}
