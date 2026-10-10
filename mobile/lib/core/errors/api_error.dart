// The error envelope of every failed API call (C2 ErrorEnvelope, architecture
// §9.8) and the app's text for each approval error (contracts/approval-protocol.md).

/// Codes from C2 `ErrorCode`; a code this build does not know maps to [unknown].
enum ApiErrorCode {
  unauthorized('UNAUTHORIZED'),
  forbidden('FORBIDDEN'),
  notFound('NOT_FOUND'),
  validationError('VALIDATION_ERROR'),
  idempotencyKeyRequired('IDEMPOTENCY_KEY_REQUIRED'),
  idempotencyConflict('IDEMPOTENCY_CONFLICT'),
  challengeInvalid('CHALLENGE_INVALID'),
  staleProposal('STALE_PROPOSAL'),
  proposalNotPending('PROPOSAL_NOT_PENDING'),
  proposalExpired('PROPOSAL_EXPIRED'),
  biometricRequired('BIOMETRIC_REQUIRED'),
  rateLimited('RATE_LIMITED'),
  cooldownActive('COOLDOWN_ACTIVE'),
  incidentBusy('INCIDENT_BUSY'),
  versionConflict('VERSION_CONFLICT'),
  benchmarkLocked('BENCHMARK_LOCKED'),
  internal('INTERNAL'),
  unknown('UNKNOWN');

  const ApiErrorCode(this.wire);
  final String wire;

  static ApiErrorCode parse(String value) =>
      values.firstWhere((c) => c.wire == value, orElse: () => unknown);
}

class ApiError implements Exception {
  const ApiError(
    this.code, {
    required this.status,
    this.message = '',
    this.details = const {},
  });

  /// Parses `{"error": {code, message, details}}`.
  factory ApiError.fromEnvelope(int status, Map<String, dynamic> json) {
    final body = json['error'] as Map<String, dynamic>;
    return ApiError(
      ApiErrorCode.parse(body['code'] as String),
      status: status,
      message: body['message'] as String,
      details: (body['details'] as Map<String, dynamic>?) ?? const {},
    );
  }

  final ApiErrorCode code;
  final int status;
  final String message;
  final Map<String, dynamic> details;

  /// Seconds to wait, for RATE_LIMITED and COOLDOWN_ACTIVE.
  int? get retryAfter => (details['retry_after'] as num?)?.toInt();

  Map<String, dynamic> toEnvelope() => {
    'error': {'code': code.wire, 'message': message, 'details': details},
  };

  /// What the user reads (approval-protocol.md, "What the app shows").
  String get userMessage => switch (code) {
    ApiErrorCode.staleProposal => 'Proposal changed — review again',
    ApiErrorCode.proposalExpired => 'This proposal expired',
    ApiErrorCode.proposalNotPending => 'Already decided',
    ApiErrorCode.challengeInvalid => 'Approval timed out — try again',
    ApiErrorCode.biometricRequired =>
      'Fingerprint or face confirmation is required',
    ApiErrorCode.rateLimited || ApiErrorCode.cooldownActive =>
      'Too many actions on this service — wait ${retryAfter ?? '?'} s',
    ApiErrorCode.idempotencyConflict ||
    ApiErrorCode.idempotencyKeyRequired => 'Something went wrong — try again',
    ApiErrorCode.unauthorized => 'Your session ended — sign in again',
    _ => message.isEmpty ? 'Something went wrong' : message,
  };

  @override
  String toString() => 'ApiError($status ${code.wire}: $message)';
}

/// Text for any error a screen shows: the ApiError's message, or a generic one.
String describeError(Object error) =>
    error is ApiError ? error.userMessage : 'Something went wrong';
