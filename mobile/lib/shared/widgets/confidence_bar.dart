import 'package:flutter/material.dart';

/// Confidence 0–1 as a bar with its percentage.
class ConfidenceBar extends StatelessWidget {
  const ConfidenceBar(this.confidence, {super.key});

  final double confidence;

  @override
  Widget build(BuildContext context) {
    final value = confidence.clamp(0.0, 1.0);
    final percent = '${(value * 100).round()} %';
    return Semantics(
      label: 'Confidence $percent',
      child: Row(
        children: [
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(value: value, minHeight: 8),
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(width: 44, child: Text(percent, textAlign: TextAlign.end)),
        ],
      ),
    );
  }
}
