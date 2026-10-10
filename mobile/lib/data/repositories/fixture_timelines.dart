// Fixture timelines for mock mode (C1 `Timeline`, contracts/fixtures/timelines/).
// They are copied into assets/fixtures/timelines/ by tool/sync_fixtures.dart;
// the copies are git-ignored, so contracts/ stays the only source.

import 'dart:convert';

import 'package:flutter/services.dart';

const timelineAssetDir = 'assets/fixtures/timelines/';

class TimelineStep {
  const TimelineStep({
    required this.delay,
    required this.awaitApproval,
    required this.event,
  });

  final Duration delay;

  /// Wait for the user to approve the pending proposal before this step.
  final bool awaitApproval;

  /// The raw C3 event message.
  final Map<String, dynamic> event;
}

class FixtureTimeline {
  const FixtureTimeline({
    required this.name,
    required this.incidentId,
    required this.steps,
    required this.finalDetail,
  });

  /// Parses a timeline and moves every timestamp by the same amount, so the
  /// first event happens at [startAt]. Expiry countdowns then run from now.
  factory FixtureTimeline.fromJson(
    Map<String, dynamic> json, {
    required DateTime startAt,
  }) {
    final steps = (json['steps'] as List).cast<Map<String, dynamic>>();
    final first = DateTime.parse(steps.first['event']['ts'] as String);
    final shifted =
        shiftTimestamps(json, startAt.toUtc().difference(first))
            as Map<String, dynamic>;
    return FixtureTimeline(
      name: shifted['name'] as String,
      incidentId: shifted['incident_id'] as String,
      steps: [
        for (final step
            in (shifted['steps'] as List).cast<Map<String, dynamic>>())
          TimelineStep(
            delay: Duration(milliseconds: step['delay_ms'] as int),
            awaitApproval: step['await_approval'] as bool? ?? false,
            event: step['event'] as Map<String, dynamic>,
          ),
      ],
      finalDetail: shifted['final'] as Map<String, dynamic>,
    );
  }

  final String name;
  final String incidentId;
  final List<TimelineStep> steps;

  /// The IncidentDetail `GET /incidents/{id}` returns after the last step.
  final Map<String, dynamic> finalDetail;
}

final _utcTimestamp = RegExp(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$');

/// A deep copy of [json] with every RFC 3339 UTC timestamp moved by [shift].
Object? shiftTimestamps(Object? json, Duration shift) => switch (json) {
  final Map<String, dynamic> map => {
    for (final entry in map.entries)
      entry.key: shiftTimestamps(entry.value, shift),
  },
  final List<dynamic> list => [
    for (final item in list) shiftTimestamps(item, shift),
  ],
  final String text when _utcTimestamp.hasMatch(text) => DateTime.parse(
    text,
  ).add(shift).toIso8601String(),
  _ => json,
};

/// Reads every timeline in [timelineAssetDir]. Throws a StateError naming
/// the sync command when there are none.
Future<List<Map<String, dynamic>>> loadTimelineAssets(
  AssetBundle bundle,
) async {
  final manifest = await AssetManifest.loadFromAssetBundle(bundle);
  final paths =
      manifest
          .listAssets()
          .where((p) => p.startsWith(timelineAssetDir) && p.endsWith('.json'))
          .toList()
        ..sort();
  if (paths.isEmpty) {
    throw StateError(
      'No fixture timelines in $timelineAssetDir. '
      'Run: dart run tool/sync_fixtures.dart',
    );
  }
  return [
    for (final path in paths)
      jsonDecode(await bundle.loadString(path)) as Map<String, dynamic>,
  ];
}
