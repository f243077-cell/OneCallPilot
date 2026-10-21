import 'package:flutter/material.dart';

import '../../data/models/enums.dart';
import '../formatting.dart';

/// The proposal's risk tier, copied from the action catalogue (C4).
class RiskBadge extends StatelessWidget {
  const RiskBadge(this.tier, {super.key});

  final RiskTier tier;

  // White text on each colour has enough contrast in both themes.
  static Color colorOf(RiskTier tier) => switch (tier) {
    RiskTier.low => const Color(0xFF2E7D32),
    RiskTier.medium => const Color(0xFFB25E00),
    RiskTier.high => const Color(0xFFB3261E),
    RiskTier.unknown => const Color(0xFF616161),
  };

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
    decoration: BoxDecoration(
      color: colorOf(tier),
      borderRadius: BorderRadius.circular(4),
    ),
    child: Text(
      '${humanize(tier)} risk',
      style: const TextStyle(
        color: Colors.white,
        fontWeight: FontWeight.bold,
        fontSize: 12,
      ),
    ),
  );
}
