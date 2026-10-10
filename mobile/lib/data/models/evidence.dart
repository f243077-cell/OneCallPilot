// C1 Evidence (domain.py). The payload has one shape per `kind`; it stays a
// map here and the evidence cards (A1.6) read it per kind.

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';

part 'evidence.g.dart';

@JsonSerializable()
class Evidence {
  const Evidence({
    required this.id,
    required this.ref,
    required this.kind,
    required this.purpose,
    required this.toolName,
    required this.summary,
    required this.payload,
    required this.suspiciousContent,
    required this.createdAt,
  });

  factory Evidence.fromJson(Map<String, dynamic> json) =>
      _$EvidenceFromJson(json);

  final String id;

  /// `E1`, `E2`, … — the only identifier the user and the model see (§6.4).
  final String ref;
  @JsonKey(unknownEnumValue: EvidenceKind.unknown)
  final EvidenceKind kind;
  @JsonKey(unknownEnumValue: EvidencePurpose.unknown)
  final EvidencePurpose purpose;
  @JsonKey(unknownEnumValue: ToolName.unknown)
  final ToolName? toolName;
  final String summary;
  final Map<String, dynamic> payload;
  final bool suspiciousContent;
  final DateTime createdAt;
}
