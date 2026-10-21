// approvalControllerProvider(proposalId) of architecture §10.3: the C8 flow
// challenge → local confirmation → approve (MOB-008), and reject with a
// reason (MOB-010).
//
// - One `Idempotency-Key` per attempt. When a call fails without an answer
//   from the server (NetworkError or any other exception), [retry] sends the
//   same body with the same key, so a request that did reach the server is
//   answered from its stored response instead of running twice.
// - An ApiError ends the attempt: the next Approve starts again from the
//   challenge with a new key. Errors that mean the proposal changed refetch
//   the incident (approval-protocol.md §7).
// - A second tap while a call is in flight is ignored.

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/models/approval.dart';
import '../../../data/models/enums.dart';
import '../../../data/models/proposal.dart';
import '../../../data/repositories/incident_repository.dart';
import '../../../shared/formatting.dart';
import '../../../shared/uuid.dart';
import '../../incident_detail/application/incident_detail_provider.dart';
import 'biometric_gate.dart';

/// The device row's ID, sent with every approval once push registration
/// (task A3.2) stores it; null until then.
final deviceIdProvider = Provider<String?>((ref) => null);

/// Makes a fresh `Idempotency-Key`; tests override it.
final idempotencyKeyProvider = Provider<String Function()>((ref) => uuidV4);

enum ApprovalPhase {
  idle,
  challenging,
  confirming,
  approving,
  rejecting,
  approved,
  rejected,
  failed,
}

class ApprovalState {
  const ApprovalState({
    this.phase = ApprovalPhase.idle,
    this.message,
    this.error,
    this.canRetry = false,
    this.executionId,
  });

  final ApprovalPhase phase;

  /// What the user reads after a failure or a cancelled confirmation.
  final String? message;
  final ApiError? error;

  /// The last call got no answer; [ApprovalController.retry] re-sends it.
  final bool canRetry;
  final String? executionId;

  bool get busy => const {
    ApprovalPhase.challenging,
    ApprovalPhase.confirming,
    ApprovalPhase.approving,
    ApprovalPhase.rejecting,
  }.contains(phase);
}

class _Attempt {
  _Attempt(this.incidentId, this.key, {this.approve, this.reason});

  final String incidentId;
  final String key;
  final ApproveRequest? approve;
  final String? reason;
}

class ApprovalController extends Notifier<ApprovalState> {
  ApprovalController(this.proposalId);

  final String proposalId;
  _Attempt? _unanswered;

  @override
  ApprovalState build() => const ApprovalState();

  IncidentRepository get _repository => ref.read(incidentRepositoryProvider);

  /// Challenge, confirm locally ([confirmTap] shows the tap dialog), approve.
  Future<void> approve(
    Proposal proposal, {
    required Future<bool> Function() confirmTap,
  }) async {
    if (state.busy) return;
    _unanswered = null;
    state = const ApprovalState(phase: ApprovalPhase.challenging);
    final ChallengeResponse challenge;
    try {
      challenge = await _repository.challenge(
        proposal.id,
        proposalFingerprint: proposal.fingerprint,
      );
    } on ApiError catch (e) {
      return _failed(proposal.incidentId, e);
    } on Exception catch (e) {
      return _set(
        ApprovalState(phase: ApprovalPhase.failed, message: describeError(e)),
      );
    }

    _set(const ApprovalState(phase: ApprovalPhase.confirming));
    final method = await _confirm(proposal, challenge, confirmTap);
    if (method == null) return;

    _unanswered = _Attempt(
      proposal.incidentId,
      ref.read(idempotencyKeyProvider)(),
      approve: ApproveRequest(
        challengeId: challenge.challengeId,
        nonce: challenge.nonce,
        proposalFingerprint: proposal.fingerprint,
        authMethod: method,
        deviceId: ref.read(deviceIdProvider),
      ),
    );
    await _send();
  }

  /// Rejects with [reason] (3–500 characters); allowed even when stale.
  Future<void> reject(Proposal proposal, String reason) async {
    if (state.busy) return;
    final text = reason.trim();
    if (!validRejectReason(text)) {
      return _set(
        const ApprovalState(
          phase: ApprovalPhase.failed,
          message: 'Give a reason of 3 to 500 characters',
        ),
      );
    }
    _unanswered = _Attempt(
      proposal.incidentId,
      ref.read(idempotencyKeyProvider)(),
      reason: text,
    );
    await _send();
  }

  /// Sends the unanswered approve or reject again, with the same key and body.
  Future<void> retry() async {
    if (state.busy || _unanswered == null) return;
    await _send();
  }

  Future<void> _send() async {
    final attempt = _unanswered!;
    final approving = attempt.approve != null;
    _set(
      ApprovalState(
        phase: approving ? ApprovalPhase.approving : ApprovalPhase.rejecting,
      ),
    );
    try {
      if (approving) {
        final accepted = await _repository.approve(
          proposalId,
          attempt.approve!,
          idempotencyKey: attempt.key,
        );
        _unanswered = null;
        _set(
          ApprovalState(
            phase: ApprovalPhase.approved,
            executionId: accepted.executionId,
          ),
        );
      } else {
        await _repository.reject(
          proposalId,
          reason: attempt.reason!,
          idempotencyKey: attempt.key,
        );
        _unanswered = null;
        _set(const ApprovalState(phase: ApprovalPhase.rejected));
      }
      _refresh(attempt.incidentId);
    } on ApiError catch (e) {
      _unanswered = null;
      _failed(attempt.incidentId, e);
    } on Exception catch (e) {
      _set(
        ApprovalState(
          phase: ApprovalPhase.failed,
          message: describeError(e),
          canRetry: true,
        ),
      );
    }
  }

  /// The confirmation the proposal needs, or null (state already says why).
  /// The stricter of the proposal's and the challenge's requirement wins; an
  /// unknown one counts as biometric.
  Future<AuthMethod?> _confirm(
    Proposal proposal,
    ChallengeResponse challenge,
    Future<bool> Function() confirmTap,
  ) async {
    final tapOnly =
        proposal.approvalRequirement == ApprovalRequirement.tap &&
        challenge.approvalRequirement == ApprovalRequirement.tap &&
        proposal.riskTier == RiskTier.low;
    if (tapOnly) {
      if (await confirmTap()) return AuthMethod.tap;
      _set(const ApprovalState());
      return null;
    }
    final outcome = await ref
        .read(biometricGateProvider)
        .confirm('Approve: ${humanize(proposal.action).toLowerCase()}');
    final message = switch (outcome) {
      BiometricOutcome.confirmed => null,
      BiometricOutcome.cancelled => 'Approval cancelled',
      BiometricOutcome.notEnrolled =>
        'This action needs a fingerprint or face unlock. '
            'Set one up in Android settings; PIN and tap are not accepted.',
      BiometricOutcome.unavailable =>
        'Fingerprint or face confirmation is unavailable right now',
    };
    if (message == null) return AuthMethod.biometric;
    _set(ApprovalState(phase: ApprovalPhase.failed, message: message));
    return null;
  }

  void _failed(String incidentId, ApiError error) {
    _set(
      ApprovalState(
        phase: ApprovalPhase.failed,
        message: error.userMessage,
        error: error,
      ),
    );
    if (const {
      ApiErrorCode.staleProposal,
      ApiErrorCode.proposalExpired,
      ApiErrorCode.proposalNotPending,
    }.contains(error.code)) {
      _refresh(incidentId);
    }
  }

  void _refresh(String incidentId) {
    if (!ref.mounted) return;
    unawaited(ref.read(incidentDetailProvider(incidentId).notifier).refresh());
  }

  void _set(ApprovalState next) {
    if (ref.mounted) state = next;
  }
}

bool validRejectReason(String reason) {
  final length = reason.trim().length;
  return length >= 3 && length <= 500;
}

final approvalControllerProvider =
    NotifierProvider.family<ApprovalController, ApprovalState, String>(
      ApprovalController.new,
    );
