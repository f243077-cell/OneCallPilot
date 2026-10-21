// MOB-008, MOB-009, MOB-010: the approval controller against the mock server
// (contracts/approval-protocol.md): challenge → local confirmation → approve,
// one Idempotency-Key per attempt reused on a network retry, and the app's
// reaction to each error code.

import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/models/enums.dart';
import 'package:oncallpilot/data/models/proposal.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';
import 'package:oncallpilot/features/action_review/application/approval_controller.dart';
import 'package:oncallpilot/features/action_review/application/biometric_gate.dart';
import 'package:oncallpilot/features/incident_detail/application/incident_detail_provider.dart';

import '../../support/fakes.dart';
import '../../support/recording_repository.dart';
import '../../support/sample_timeline.dart';

class FakeGate implements BiometricGate {
  BiometricOutcome next = BiometricOutcome.confirmed;
  final reasons = <String>[];

  @override
  Future<BiometricOutcome> confirm(String reason) async {
    reasons.add(reason);
    return next;
  }
}

void main() {
  late MockIncidentRepository server;
  late RecordingRepository repo;
  late FakeGate gate;
  late ProviderContainer container;
  late int taps;
  late bool tapAnswer;

  Future<Proposal> start({String riskTier = 'medium'}) async {
    server = MockIncidentRepository(
      [sampleTimeline(riskTier: riskTier)],
      delay: (_) async {},
      random: Random(7),
    )..start();
    await settle(); // the replay stops at the approval gate
    repo = RecordingRepository(server);
    gate = FakeGate();
    var keys = 0;
    container = ProviderContainer(
      overrides: [
        incidentRepositoryProvider.overrideWithValue(repo),
        biometricGateProvider.overrideWithValue(gate),
        idempotencyKeyProvider.overrideWithValue(() => 'key-${++keys}'),
      ],
    );
    addTearDown(container.dispose);
    container.listen(incidentDetailProvider(incidentId), (_, _) {});
    final detail = await container.read(
      incidentDetailProvider(incidentId).future,
    );
    repo.detailCalls = 0;
    return detail.proposals.single;
  }

  ApprovalController controller() =>
      container.read(approvalControllerProvider(proposalId).notifier);
  ApprovalState state() =>
      container.read(approvalControllerProvider(proposalId));

  setUp(() {
    taps = 0;
    tapAnswer = true;
  });

  Future<bool> confirmTap() async {
    taps++;
    return tapAnswer;
  }

  Map<String, dynamic> body(int i) =>
      jsonDecode(repo.approves[i].$2) as Map<String, dynamic>;

  group('approve', () {
    test('low risk: challenge, tap dialog, approve with tap', () async {
      final proposal = await start(riskTier: 'low');
      await controller().approve(proposal, confirmTap: confirmTap);
      expect(taps, 1);
      expect(gate.reasons, isEmpty);
      expect(repo.challenges, [fingerprint]);
      expect(repo.approves.single.$1, 'key-1');
      expect(body(0)['auth_method'], 'tap');
      expect(body(0)['proposal_fingerprint'], fingerprint);
      expect(state().phase, ApprovalPhase.approved);
      expect(state().executionId, executionId);
    });

    test('a cancelled tap dialog sends nothing', () async {
      final proposal = await start(riskTier: 'low');
      tapAnswer = false;
      await controller().approve(proposal, confirmTap: confirmTap);
      expect(repo.approves, isEmpty);
      expect(state().phase, ApprovalPhase.idle);
    });

    test(
      'MOB-009: medium risk uses local_auth, never the tap dialog',
      () async {
        final proposal = await start();
        await controller().approve(proposal, confirmTap: confirmTap);
        expect(gate.reasons, ['Approve: rollback deploy']);
        expect(taps, 0);
        expect(body(0)['auth_method'], 'biometric');
        expect(state().phase, ApprovalPhase.approved);
      },
    );

    test('MOB-009: no biometric enrolled blocks the approval', () async {
      final proposal = await start();
      gate.next = BiometricOutcome.notEnrolled;
      await controller().approve(proposal, confirmTap: confirmTap);
      expect(taps, 0, reason: 'never falls back to tap');
      expect(repo.approves, isEmpty);
      expect(state().phase, ApprovalPhase.failed);
      expect(state().message, contains('PIN and tap are not accepted'));
    });

    test('a cancelled or unavailable biometric sends nothing', () async {
      final proposal = await start();
      for (final outcome in [
        BiometricOutcome.cancelled,
        BiometricOutcome.unavailable,
      ]) {
        gate.next = outcome;
        await controller().approve(proposal, confirmTap: confirmTap);
        expect(repo.approves, isEmpty);
        expect(state().phase, ApprovalPhase.failed);
      }
    });

    test('an unknown approval requirement counts as biometric', () async {
      final proposal = await start(riskTier: 'low');
      final odd = Proposal.fromJson({
        ...jsonDecode(jsonEncode(_json(proposal))) as Map<String, dynamic>,
        'approval_requirement': 'retina_scan',
      });
      expect(odd.approvalRequirement, ApprovalRequirement.unknown);
      await controller().approve(odd, confirmTap: confirmTap);
      expect(taps, 0);
      expect(gate.reasons, hasLength(1));
    });

    test(
      'MOB-008: a network error, then retry, sends the same key and body',
      () async {
        final proposal = await start();
        repo.dropNext(MockOperation.approve, Drop.beforeServer);
        await controller().approve(proposal, confirmTap: confirmTap);
        expect(state().phase, ApprovalPhase.failed);
        expect(state().canRetry, isTrue);
        expect(state().message, contains('No connection'));
        await controller().retry();
        expect(repo.approves, hasLength(2));
        expect(repo.approves[1], repo.approves[0]);
        expect(repo.challenges, hasLength(1), reason: 'no new challenge');
        expect(gate.reasons, hasLength(1), reason: 'no new confirmation');
        expect(state().phase, ApprovalPhase.approved);
      },
    );

    test(
      'MOB-008: a lost answer is replayed by the server, not run twice',
      () async {
        final proposal = await start();
        repo.dropNext(MockOperation.approve, Drop.afterServer);
        await controller().approve(proposal, confirmTap: confirmTap);
        expect(state().canRetry, isTrue);
        await controller().retry();
        expect(repo.approves.map((a) => a.$1), ['key-1', 'key-1']);
        expect(state().phase, ApprovalPhase.approved);
        expect(state().executionId, executionId);
      },
    );

    test(
      'MOB-008: 409 STALE_PROPOSAL refetches and asks for a new review',
      () async {
        final proposal = await start();
        server.failNext(MockOperation.approve, ApiErrorCode.staleProposal);
        await controller().approve(proposal, confirmTap: confirmTap);
        expect(state().message, 'Proposal changed — review again');
        expect(state().canRetry, isFalse);
        await settle();
        expect(repo.detailCalls, greaterThan(0));
      },
    );

    test('expired and already-decided proposals refetch too', () async {
      final proposal = await start();
      for (final (code, text) in [
        (ApiErrorCode.proposalExpired, 'This proposal expired'),
        (ApiErrorCode.proposalNotPending, 'Already decided'),
      ]) {
        repo.detailCalls = 0;
        server.failNext(MockOperation.challenge, code);
        await controller().approve(proposal, confirmTap: confirmTap);
        expect(state().message, text);
        await settle();
        expect(repo.detailCalls, greaterThan(0));
      }
    });

    test(
      'CHALLENGE_INVALID: the next approve starts over with a new key',
      () async {
        final proposal = await start();
        server.failNext(MockOperation.approve, ApiErrorCode.challengeInvalid);
        await controller().approve(proposal, confirmTap: confirmTap);
        expect(state().message, 'Approval timed out — try again');
        expect(state().canRetry, isFalse);
        await controller().approve(proposal, confirmTap: confirmTap);
        expect(repo.challenges, hasLength(2));
        expect(repo.approves.map((a) => a.$1), ['key-1', 'key-2']);
        expect(state().phase, ApprovalPhase.approved);
      },
    );

    test('RATE_LIMITED and COOLDOWN_ACTIVE show the wait', () async {
      final proposal = await start();
      server.failNext(
        MockOperation.approve,
        ApiErrorCode.rateLimited,
        retryAfter: 42,
      );
      await controller().approve(proposal, confirmTap: confirmTap);
      expect(state().message, 'Too many actions on this service — wait 42 s');
      server.failNext(
        MockOperation.approve,
        ApiErrorCode.cooldownActive,
        retryAfter: 7,
      );
      await controller().approve(proposal, confirmTap: confirmTap);
      expect(state().message, 'Too many actions on this service — wait 7 s');
    });

    test('a second tap while a call is in flight is ignored', () async {
      final proposal = await start();
      repo.holdChallenge = Completer<void>();
      final first = controller().approve(proposal, confirmTap: confirmTap);
      final second = controller().approve(proposal, confirmTap: confirmTap);
      expect(state().busy, isTrue);
      repo.holdChallenge!.complete();
      await Future.wait([first, second]);
      expect(repo.challenges, hasLength(1));
      expect(repo.approves, hasLength(1));
    });
  });

  group('reject (MOB-010)', () {
    test('needs a reason of 3 to 500 characters', () async {
      final proposal = await start();
      for (final reason in ['', 'no', '  x  ', 'y' * 501]) {
        await controller().reject(proposal, reason);
        expect(state().phase, ApprovalPhase.failed);
        expect(state().message, 'Give a reason of 3 to 500 characters');
      }
      expect(repo.rejects, isEmpty);
      expect(validRejectReason('abc'), isTrue);
      expect(validRejectReason('y' * 500), isTrue);
    });

    test('rejects with the trimmed reason and refetches', () async {
      final proposal = await start();
      await controller().reject(proposal, '  wrong release  ');
      expect(repo.rejects.single, ('key-1', 'wrong release'));
      expect(state().phase, ApprovalPhase.rejected);
      await settle();
      final detail = await container.read(
        incidentDetailProvider(incidentId).future,
      );
      expect(detail.status, IncidentStatus.escalated);
      expect(detail.statusReason, EscalationReason.proposalRejected);
    });

    test('a network error, then retry, sends the same key', () async {
      final proposal = await start();
      repo.dropNext(MockOperation.reject, Drop.afterServer);
      await controller().reject(proposal, 'not now');
      expect(state().canRetry, isTrue);
      await controller().retry();
      expect(repo.rejects.map((r) => r.$1), ['key-1', 'key-1']);
      expect(state().phase, ApprovalPhase.rejected);
    });
  });
}

/// A Proposal back to C1 JSON, for tests that change one field.
Map<String, dynamic> _json(Proposal p) => {
  'id': p.id,
  'incident_id': p.incidentId,
  'kind': 'primary',
  'parent_proposal_id': p.parentProposalId,
  'action': 'rollback_deploy',
  'params': p.params,
  'risk_tier': p.riskTier.name,
  'approval_requirement': 'tap',
  'expected_effect': p.expectedEffect,
  'rationale': p.rationale,
  'evidence_refs': p.evidenceRefs,
  'rollback_step': null,
  'dry_run_result': {
    'would_change': <Object>[],
    'current': {
      'active_release': null,
      'running_replicas': null,
      'containers': <Object>[],
      'cache_keys': null,
    },
    'preconditions': {
      'rate_limit_ok': true,
      'cooldown_ok': true,
      'slots_available': true,
    },
    'predicted_effect': p.dryRunResult.predictedEffect,
    'warnings': <String>[],
    'state_fingerprint': p.dryRunResult.stateFingerprint,
  },
  'dry_run_at': p.dryRunAt.toIso8601String(),
  'fingerprint': p.fingerprint,
  'status': 'pending',
  'status_reason': null,
  'expires_at': p.expiresAt.toIso8601String(),
  'created_at': p.createdAt.toIso8601String(),
};
