// Phase 0: "the app parses the contract fixtures" (MOB-020, TEST-009).
//
// Reads every fixture in contracts/fixtures/ (or $OCP_CONTRACTS_DIR/fixtures):
// timelines, WebSocket messages, and the deploy ledger. Until C1/C3 are merged
// into main the timelines and ws folders do not exist on this branch, and the
// group is skipped with a message; run it against another checkout with
//   OCP_CONTRACTS_DIR=<path to contracts> flutter test test/contracts

import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/models/approval.dart';
import 'package:oncallpilot/data/models/enums.dart' as dto;
import 'package:oncallpilot/data/models/incident.dart';
import 'package:oncallpilot/data/models/ws_event.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';

import '../support/contract_fixtures.dart';
import '../support/fakes.dart';

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

  // A1.5: the real DTOs (lib/data/models) parse the same fixtures, and every
  // timeline replays to its final state through MockIncidentRepository.
  group('DTOs and mock replay', skip: skip, () {
    for (final file in jsonFiles(Directory('${dir.path}/ws'))) {
      final name = file.uri.pathSegments.last;
      final json = readJsonObject(file);
      if (json['type'] != 'event') continue;
      test('WsEvent parses $name', () {
        expect(WsEvent.fromJson(json).event, isNot(dto.WsEventName.unknown));
      });
    }
    for (final file in jsonFiles(Directory('${dir.path}/timelines'))) {
      final name = file.uri.pathSegments.last;
      test('$name replays to its final IncidentDetail', () async {
        final json = readJsonObject(file);
        final approvals = (json['steps'] as List)
            .where((s) => (s as Map)['await_approval'] == true)
            .length;
        final repo = MockIncidentRepository(
          [json],
          delay: (_) async {},
          random: Random(3),
        )..start();
        final incidentId = json['incident_id'] as String;
        for (var approved = 0; approved < approvals; approved++) {
          await settle();
          final pending = (await repo.getIncident(incidentId)).proposals
              .firstWhere((p) => p.status == dto.ProposalStatus.pending);
          final challenge = await repo.challenge(
            pending.id,
            proposalFingerprint: pending.fingerprint,
          );
          await repo.approve(
            pending.id,
            ApproveRequest(
              challengeId: challenge.challengeId,
              nonce: challenge.nonce,
              proposalFingerprint: pending.fingerprint,
              authMethod: dto.AuthMethod.biometric,
              deviceId: null,
            ),
            idempotencyKey: 'key-$approved',
          );
        }
        await settle();
        final detail = await repo.getIncident(incidentId);
        final expected = IncidentDetail.fromJson(
          json['final'] as Map<String, dynamic>,
        );
        expect(detail.stateVersion, expected.stateVersion);
        expect(detail.status, expected.status);
        expect(detail.status, isNot(dto.IncidentStatus.unknown));
        expect(
          detail.evidence.map((e) => e.ref),
          expected.evidence.map((e) => e.ref),
        );
        repo.dispose();
      });
    }
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
