// C8 approval bodies (approval.py; contracts/approval-protocol.md).

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';

part 'approval.g.dart';

@JsonSerializable()
class ChallengeResponse {
  const ChallengeResponse({
    required this.challengeId,
    required this.nonce,
    required this.expiresAt,
    required this.approvalRequirement,
  });

  factory ChallengeResponse.fromJson(Map<String, dynamic> json) =>
      _$ChallengeResponseFromJson(json);

  final String challengeId;
  final String nonce;
  final DateTime expiresAt;
  @JsonKey(unknownEnumValue: ApprovalRequirement.unknown)
  final ApprovalRequirement approvalRequirement;
}

/// Body of `POST /proposals/{id}/approve`; sent with an `Idempotency-Key`.
@JsonSerializable(createToJson: true, createFactory: false)
class ApproveRequest {
  const ApproveRequest({
    required this.challengeId,
    required this.nonce,
    required this.proposalFingerprint,
    required this.authMethod,
    required this.deviceId,
  });

  final String challengeId;
  final String nonce;
  final String proposalFingerprint;
  final AuthMethod authMethod;
  final String? deviceId;

  Map<String, dynamic> toJson() => _$ApproveRequestToJson(this);
}

@JsonSerializable()
class ApproveAccepted {
  const ApproveAccepted({required this.executionId, required this.status});

  factory ApproveAccepted.fromJson(Map<String, dynamic> json) =>
      _$ApproveAcceptedFromJson(json);

  final String executionId;

  /// Always `queued`.
  final String status;
}

@JsonSerializable()
class RejectResponse {
  const RejectResponse({required this.proposalId, required this.status});

  factory RejectResponse.fromJson(Map<String, dynamic> json) =>
      _$RejectResponseFromJson(json);

  final String proposalId;

  /// Always `rejected`.
  final String status;
}
