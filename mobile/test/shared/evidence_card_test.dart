// MOB-005: one EvidenceCard variant per evidence kind.

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/models/evidence.dart';
import 'package:oncallpilot/shared/widgets/evidence_card.dart';

import '../support/sample_incident.dart';

Evidence byRef(String ref) =>
    Evidence.fromJson(allEvidence.firstWhere((e) => e['ref'] == ref));

Future<void> pumpCard(
  WidgetTester tester,
  Evidence evidence, {
  DateTime? start,
  DateTime? end,
}) => tester.pumpWidget(
  MaterialApp(
    home: Scaffold(
      body: SingleChildScrollView(
        child: EvidenceCard(
          evidence: evidence,
          incidentStart: start,
          incidentEnd: end,
        ),
      ),
    ),
  ),
);

void main() {
  testWidgets('alert signal: value against baseline', (tester) async {
    await pumpCard(tester, byRef('E1'));
    expect(find.text('Alert signal'), findsOneWidget);
    expect(
      find.text('Error rate: 0.31 (baseline 0.004), z 11.2'),
      findsOneWidget,
    );
  });

  testWidgets('log: error and critical lines are highlighted', (tester) async {
    await pumpCard(tester, byRef('E2'));
    expect(find.text('3 of 41 matching lines'), findsOneWidget);
    final rows = tester
        .widgetList<LogLineRow>(find.byType(LogLineRow))
        .toList();
    expect(rows.map((r) => r.highlighted), [false, true, true]);
    expect(find.textContaining('checkout failed'), findsOneWidget);
    expect(find.text('IndexError ×39'), findsOneWidget);
  });

  testWidgets('log: an empty result says so', (tester) async {
    await pumpCard(tester, byRef('E7'));
    expect(find.text('No matching lines (0 in the window)'), findsOneWidget);
    expect(find.text('disproof check'), findsOneWidget);
  });

  testWidgets('metric: line, shaded incident span, change point', (
    tester,
  ) async {
    await pumpCard(
      tester,
      byRef('E3'),
      start: openedAt.subtract(const Duration(seconds: 30)),
    );
    final chart = tester.widget<LineChart>(find.byType(LineChart)).data;
    expect(chart.lineBarsData.single.spots, hasLength(6));
    final span = chart.rangeAnnotations.verticalRangeAnnotations.single;
    expect((span.x1, span.x2), (30.0, 75.0)); // anomaly start to the last point
    expect(chart.extraLinesData.verticalLines.single.x, 45.0);
    expect(find.text('Error rate · Api'), findsOneWidget);
    expect(
      find.textContaining('Baseline 0.40 % · peak 31.0 % · +7650 %'),
      findsOneWidget,
    );
  });

  testWidgets('metric: no points shows the note instead of a chart', (
    tester,
  ) async {
    final json = Map<String, dynamic>.of(allEvidence[2]);
    json['payload'] = {
      ...json['payload'] as Map<String, dynamic>,
      'points': <Object>[],
      'change_point_at': null,
      'note': 'no series for this service',
    };
    await pumpCard(tester, Evidence.fromJson(json));
    expect(find.byType(LineChart), findsNothing);
    expect(find.text('no series for this service'), findsOneWidget);
  });

  testWidgets('commit: release, short SHA, message, time', (tester) async {
    await pumpCard(tester, byRef('E4'));
    expect(find.text('Api 1.5.0 · Deploy by Ci'), findsOneWidget);
    expect(
      find.text('checkout: compute totals with new pricing rounding'),
      findsOneWidget,
    );
    expect(find.textContaining('e56a282e7523 · '), findsOneWidget);
  });

  testWidgets('health: container states and the probe', (tester) async {
    await pumpCard(tester, byRef('E5'));
    expect(
      find.text('cs-api-150-1 (1.5.0): Running, Healthy, 0 restarts'),
      findsOneWidget,
    );
    expect(find.text('Health probe OK (200, 4 ms)'), findsOneWidget);
  });

  testWidgets('runbook: heading and snippet', (tester) async {
    await pumpCard(tester, byRef('E6'));
    expect(find.text('Errors after a deploy'), findsOneWidget);
    expect(find.text('runbooks/api-errors.md'), findsOneWidget);
    expect(
      find.text('Compare the error start with the last deploy time.'),
      findsOneWidget,
    );
  });
}
