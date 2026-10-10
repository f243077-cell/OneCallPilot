// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'approval.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

ChallengeResponse _$ChallengeResponseFromJson(Map<String, dynamic> json) =>
    ChallengeResponse(
      challengeId: json['challenge_id'] as String,
      nonce: json['nonce'] as String,
      expiresAt: DateTime.parse(json['expires_at'] as String),
      approvalRequirement: $enumDecode(
        _$ApprovalRequirementEnumMap,
        json['approval_requirement'],
        unknownValue: ApprovalRequirement.unknown,
      ),
    );

const _$ApprovalRequirementEnumMap = {
  ApprovalRequirement.tap: 'tap',
  ApprovalRequirement.biometric: 'biometric',
  ApprovalRequirement.unknown: 'unknown',
};

Map<String, dynamic> _$ApproveRequestToJson(ApproveRequest instance) =>
    <String, dynamic>{
      'challenge_id': instance.challengeId,
      'nonce': instance.nonce,
      'proposal_fingerprint': instance.proposalFingerprint,
      'auth_method': _$AuthMethodEnumMap[instance.authMethod]!,
      'device_id': instance.deviceId,
    };

const _$AuthMethodEnumMap = {
  AuthMethod.biometric: 'biometric',
  AuthMethod.tap: 'tap',
};

ApproveAccepted _$ApproveAcceptedFromJson(Map<String, dynamic> json) =>
    ApproveAccepted(
      executionId: json['execution_id'] as String,
      status: json['status'] as String,
    );

RejectResponse _$RejectResponseFromJson(Map<String, dynamic> json) =>
    RejectResponse(
      proposalId: json['proposal_id'] as String,
      status: json['status'] as String,
    );
