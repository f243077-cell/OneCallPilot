// C1 Hypothesis (domain.py).

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';

part 'hypothesis.g.dart';

@JsonSerializable()
class Hypothesis {
  const Hypothesis({
    required this.id,
    required this.rank,
    required this.summary,
    required this.rootCauseCategory,
    required this.confidence,
    required this.confidenceInitial,
    required this.evidenceRefs,
    required this.status,
    required this.reflectionNotes,
  });

  factory Hypothesis.fromJson(Map<String, dynamic> json) =>
      _$HypothesisFromJson(json);

  final String id;
  final int rank;
  final String summary;
  @JsonKey(unknownEnumValue: RootCauseCategory.unknown)
  final RootCauseCategory rootCauseCategory;

  /// After reflection (AI-016); [confidenceInitial] is before it.
  final double confidence;
  final double confidenceInitial;
  final List<String> evidenceRefs;
  @JsonKey(unknownEnumValue: HypothesisStatus.unknown)
  final HypothesisStatus status;
  final String? reflectionNotes;
}
