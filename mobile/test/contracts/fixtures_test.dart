// Phase 0: "the app parses the contract fixtures" (MOB-020, TEST-009).
//
// Reads every fixture in contracts/fixtures/ (or $OCP_CONTRACTS_DIR/fixtures):
// timelines, WebSocket messages, and the deploy ledger. Until C1/C3 are merged
// into main the timelines and ws folders do not exist on this branch, and the
// group is skipped with a message; run it against another checkout with
//   OCP_CONTRACTS_DIR=<path to contracts> flutter test test/contracts

import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

import '../support/contract_fixtures.dart';

void main() {
  final dir = contractFixturesDir();
  final shared = hasSharedFixtures(dir);
  final skip = shared
      ? false
      : 'C1/C3 fixtures not found in ${dir.path} (contracts not merged yet)';

  group('timelines', skip: skip, () {
    final files = jsonFiles(Directory('${dir.path}/timelines'));
    test('there are timelines', () => expect(files, isNotEmpty));
    for (final file in files) {
      final name = file.uri.pathSegments.last;
      test(
        name,
        () => expect(parseTimeline(readJsonObject(file), name), greaterThan(0)),
      );
    }
  });

  group('websocket messages', skip: skip, () {
    final files = jsonFiles(Directory('${dir.path}/ws'));
    test('there are messages', () => expect(files, isNotEmpty));
    for (final file in files) {
      final name = file.uri.pathSegments.last;
      test(name, () {
        final event = parseWsMessage(readJsonObject(file), name);
        expect(event, isNot(WsEventName.unknown));
      });
    }
  });

  group('deploy ledger', () {
    final file = File('${dir.path}/ledger/deploys.jsonl');
    test(
      'every line parses',
      () {
        final lines = file
            .readAsLinesSync()
            .where((l) => l.trim().isNotEmpty)
            .toList();
        expect(lines, isNotEmpty);
        for (final line in lines) {
          final record = jsonDecode(line) as Map<String, dynamic>;
          req<String>(record, 'release', 'ledger');
          req<String>(record, 'commit_message', 'ledger');
          reqTime(record, 'deployed_at', 'ledger');
        }
      },
      skip: file.existsSync() ? false : 'no ledger fixture in ${dir.path}',
    );
  });

  test('unknown enum values map to unknown (MOB-020)', () {
    expect(WsEventName.parse('incident.reopened'), WsEventName.unknown);
    expect(IncidentStatus.parse('paused'), IncidentStatus.unknown);
    expect(
      IncidentStatus.parse('awaiting_approval'),
      IncidentStatus.awaitingApproval,
    );
  });
}
