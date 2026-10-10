import 'package:flutter/material.dart';

import '../../data/models/enums.dart';
import '../formatting.dart';

class SeverityBadge extends StatelessWidget {
  const SeverityBadge(this.severity, {super.key});

  final Severity severity;

  // White text on each colour has enough contrast in both themes.
  static Color colorOf(Severity severity) => switch (severity) {
    Severity.sev1 => const Color(0xFFB3261E),
    Severity.sev2 => const Color(0xFFB25E00),
    Severity.sev3 => const Color(0xFF3B5BA9),
    Severity.unknown => const Color(0xFF616161),
  };

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
    decoration: BoxDecoration(
      color: colorOf(severity),
      borderRadius: BorderRadius.circular(4),
    ),
    child: Text(
      severityLabel(severity),
      style: const TextStyle(
        color: Colors.white,
        fontWeight: FontWeight.bold,
        fontSize: 12,
      ),
    ),
  );
}
