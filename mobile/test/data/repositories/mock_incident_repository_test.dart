// MOB-017: mock mode replays timelines offline and answers challenge, approve
// and reject with the checks and error codes of the approval protocol (C8).

import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/models/approval.dart';
import 'package:oncallpilot/data/models/enums.dart';
import 'package:oncallpilot/data/models/ws_event.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';

import '../../support/fakes.dart';
import '../../support/sample_timeline.dart';

Matcher apiError(ApiErrorCode code, int status) => isA<ApiError>()
    .having((e) => e.code, 'code', code)
    .having((e) => e.status, 'status', status);

void main() {
  late DateTime now;
  late MockIncidentRepository repo;
  late List<WsEvent> events;

  MockIncidentRepository build({String riskTier = 'medium'}) {
    final repository = MockIncidentRepository(
      [sampleTimeline(riskTier: riskTier)],
      clock: () => now,
      delay: (_) async {},
      random: Random(7),
    );
    events = [];
    repository.events().listen(events.add);
    return repository..start();
  }

  ApproveRequest approval(
    ChallengeResponse challenge, {
    AuthMethod method = AuthMethod.biometric,
    String? nonce,
    String? fingerprintOverride,
  }) => ApproveRequest(
    challengeId: challenge.challengeId,
    nonce: nonce ?? challenge.nonce,
    proposalFingerprint: fingerprintOverride ?? fingerprint,
    authMethod: method,
    deviceId: null,
  );

  setUp(() async {
    now = DateTime.utc(2026, 10, 10, 12);
    repo = build();
    await settle();
  });

  tearDown(() => repo.dispose());

  test('replays events in order and waits at the approval step', () async {
    expect(events.map((e) => e.stateVersion), [1, 2, 3, 4, 5]);
    final detail = await repo.getIncident(incidentId);
    expect(detail.status, IncidentStatus.awaitingApproval);
    expect(detail.pendingProposalId, proposalId);
    expect(detail.evidence.single.ref, 'E1');
    expect(
      detail.topHypothesis!.rootCauseCategory,
      RootCauseCategory.badDeploy,
    );
    expect(detail.proposals.single.status, ProposalStatus.pending);
    // Timestamps are moved so the incident opens now.
    expect(detail.openedAt, now);
    expect(
      detail.proposals.single.expiresAt,
      now.add(const Duration(seconds: 1020)),
    );
    expect((await repo.listIncidents()).single.id, incidentId);
  });

  test('challenge checks: unknown, stale, then a valid challenge', () async {
    await expectLater(
      repo.challenge('nope', proposalFingerprint: fingerprint),
      throwsA(apiError(ApiErrorCode.notFound, 404)),
    );
    await expectLater(
      repo.challenge(proposalId, proposalFingerprint: '0' * 64),
      throwsA(apiError(ApiErrorCode.staleProposal, 409)),
    );
    final challenge = await repo.challenge(
      proposalId,
      proposalFingerprint: fingerprint,
    );
    expect(challenge.approvalRequirement, ApprovalRequirement.biometric);
    expect(challenge.nonce, matches(RegExp(r'^[0-9a-f]{64}$')));
    expect(challenge.expiresAt, now.add(challengeTtl));
  });

  test('an expired proposal cannot be challenged', () async {
    now = now.add(const Duration(minutes: 20));
    await expectLater(
      repo.challenge(proposalId, proposalFingerprint: fingerprint),
      throwsA(apiError(ApiErrorCode.proposalExpired, 409)),
    );
  });

  test('approve follows the protocol order and finishes the timeline', () async {
    var challenge = await repo.challenge(
      proposalId,
      proposalFingerprint: fingerprint,
    );
    await expectLater(
      repo.approve(proposalId, approval(challenge), idempotencyKey: ''),
      throwsA(apiError(ApiErrorCode.idempotencyKeyRequired, 400)),
    );
    await expectLater(
      repo.approve(
        proposalId,
        approval(challenge, nonce: '0' * 64),
        idempotencyKey: 'k1',
      ),
      throwsA(apiError(ApiErrorCode.challengeInvalid, 409)),
    );
    // A challenge is single use: the tap attempt below consumes it.
    await expectLater(
      repo.approve(
        proposalId,
        approval(challenge, method: AuthMethod.tap),
        idempotencyKey: 'k2',
      ),
      throwsA(apiError(ApiErrorCode.biometricRequired, 403)),
    );
    await expectLater(
      repo.approve(proposalId, approval(challenge), idempotencyKey: 'k3'),
      throwsA(apiError(ApiErrorCode.challengeInvalid, 409)),
    );

    challenge = await repo.challenge(
      proposalId,
      proposalFingerprint: fingerprint,
    );
    final accepted = await repo.approve(
      proposalId,
      approval(challenge),
      idempotencyKey: 'k4',
    );
    expect(accepted.executionId, executionId);
    expect(accepted.status, 'queued');

    // Same key, same body: the stored response. Same key, other body: conflict.
    final replayed = await repo.approve(
      proposalId,
      approval(challenge),
      idempotencyKey: 'k4',
    );
    expect(replayed.executionId, executionId);
    await expectLater(
      repo.approve(
        proposalId,
        approval(challenge, method: AuthMethod.tap),
        idempotencyKey: 'k4',
      ),
      throwsA(apiError(ApiErrorCode.idempotencyConflict, 422)),
    );
    await expectLater(
      repo.challenge(proposalId, proposalFingerprint: fingerprint),
      throwsA(apiError(ApiErrorCode.proposalNotPending, 409)),
    );

    await settle();
    expect(events.last.event, WsEventName.incidentResolved);
    final detail = await repo.getIncident(incidentId);
    expect(detail.status, IncidentStatus.resolved);
    expect(detail.resolution, Resolution.actionSucceeded);
    expect(detail.stateVersion, 10);
    expect(detail.executions.single.status, ExecutionStatus.succeeded);
  });

  test('a stale fingerprint at approve is refused', () async {
    final challenge = await repo.challenge(
      proposalId,
      proposalFingerprint: fingerprint,
    );
    await expectLater(
      repo.approve(
        proposalId,
        approval(challenge, fingerprintOverride: '1' * 64),
        idempotencyKey: 'k',
      ),
      throwsA(apiError(ApiErrorCode.staleProposal, 409)),
    );
  });

  test('a low-tier proposal is approved with a tap', () async {
    repo.dispose();
    repo = build(riskTier: 'low');
    await settle();
    final challenge = await repo.challenge(
      proposalId,
      proposalFingerprint: fingerprint,
    );
    expect(challenge.approvalRequirement, ApprovalRequirement.tap);
    final accepted = await repo.approve(
      proposalId,
      approval(challenge, method: AuthMethod.tap),
      idempotencyKey: 'k',
    );
    expect(accepted.status, 'queued');
  });

  test('every error code can be simulated, once', () async {
    for (final code in ApiErrorCode.values) {
      repo.failNext(MockOperation.challenge, code, retryAfter: 90);
      await expectLater(
        repo.challenge(proposalId, proposalFingerprint: fingerprint),
        throwsA(apiError(code, MockIncidentRepository.statusFor(code))),
      );
    }
    repo.failNext(
      MockOperation.approve,
      ApiErrorCode.cooldownActive,
      retryAfter: 90,
    );
    final challenge = await repo.challenge(
      proposalId,
      proposalFingerprint: fingerprint,
    );
    try {
      await repo.approve(proposalId, approval(challenge), idempotencyKey: 'k');
      fail('expected COOLDOWN_ACTIVE');
    } on ApiError catch (error) {
      expect(error.status, 429);
      expect(error.retryAfter, 90);
      expect(error.userMessage, contains('wait 90 s'));
    }
    // The forced error is used up; the challenge is still valid.
    final accepted = await repo.approve(
      proposalId,
      approval(challenge),
      idempotencyKey: 'k',
    );
    expect(accepted.executionId, executionId);
  });

  test(
    'reject validates, escalates the incident and stops the replay',
    () async {
      await expectLater(
        repo.reject(proposalId, reason: 'no', idempotencyKey: 'r1'),
        throwsA(apiError(ApiErrorCode.validationError, 422)),
      );
      await expectLater(
        repo.reject(proposalId, reason: 'not now', idempotencyKey: ''),
        throwsA(apiError(ApiErrorCode.idempotencyKeyRequired, 400)),
      );
      final rejected = await repo.reject(
        proposalId,
        reason: 'wrong target',
        idempotencyKey: 'r2',
      );
      expect(rejected.status, 'rejected');
      await expectLater(
        repo.reject(proposalId, reason: 'again', idempotencyKey: 'r3'),
        throwsA(apiError(ApiErrorCode.proposalNotPending, 409)),
      );
      await settle();
      final detail = await repo.getIncident(incidentId);
      expect(detail.status, IncidentStatus.escalated);
      expect(detail.statusReason, EscalationReason.proposalRejected);
      expect(detail.proposals.single.status, ProposalStatus.rejected);
      expect(detail.pendingProposalId, isNull);
      expect(events.map((e) => e.stateVersion).skip(5), [6, 7]);
    },
  );

  test('an unknown incident is NOT_FOUND', () async {
    await expectLater(
      repo.getIncident('missing'),
      throwsA(apiError(ApiErrorCode.notFound, 404)),
    );
  });
}
