// TEST-011 error mapping (MOB-008, approval-protocol.md §7): every C2 code the
// app can meet reads as the protocol's message; a code this build does not
// know is `unknown`, never a crash.

import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';

void main() {
  ApiError error(String code, {Map<String, dynamic>? details}) =>
      ApiError.fromEnvelope(409, {
        'error': {'code': code, 'message': 'server text', 'details': ?details},
      });

  test('approval codes read as the protocol says', () {
    expect(
      error('STALE_PROPOSAL').userMessage,
      'Proposal changed — review again',
    );
    expect(error('PROPOSAL_EXPIRED').userMessage, 'This proposal expired');
    expect(error('PROPOSAL_NOT_PENDING').userMessage, 'Already decided');
    expect(
      error('CHALLENGE_INVALID').userMessage,
      'Approval timed out — try again',
    );
    expect(
      error('BIOMETRIC_REQUIRED').userMessage,
      'Fingerprint or face confirmation is required',
    );
    expect(
      error('RATE_LIMITED', details: {'retry_after': 30}).userMessage,
      'Too many actions on this service — wait 30 s',
    );
    expect(
      error('COOLDOWN_ACTIVE').userMessage,
      'Too many actions on this service — wait ? s',
    );
    expect(
      error('IDEMPOTENCY_CONFLICT').userMessage,
      'Something went wrong — try again',
    );
    expect(
      error('IDEMPOTENCY_KEY_REQUIRED').userMessage,
      'Something went wrong — try again',
    );
    expect(
      error('UNAUTHORIZED').userMessage,
      'Your session ended — sign in again',
    );
  });

  test('other codes show the server message; unknown codes are unknown', () {
    expect(error('INCIDENT_BUSY').userMessage, 'server text');
    final unknown = error('BRAND_NEW_CODE');
    expect(unknown.code, ApiErrorCode.unknown);
    expect(unknown.userMessage, 'server text');
  });

  test('describeError covers ApiError, NetworkError and anything else', () {
    expect(describeError(error('PROPOSAL_EXPIRED')), 'This proposal expired');
    expect(
      describeError(const NetworkError()),
      'No connection — check the network and try again',
    );
    expect(describeError(StateError('bug')), 'Something went wrong');
  });

  test('the envelope round-trips', () {
    final e = error('RATE_LIMITED', details: {'retry_after': 5});
    expect(ApiError.fromEnvelope(429, e.toEnvelope()).retryAfter, 5);
  });
}
