// The metric evidence chart (MOB-005): the series as a line, the incident's
// span shaded, and the change point marked with a dashed line.

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../../data/models/evidence_payloads.dart';
import '../formatting.dart';

class MetricChart extends StatelessWidget {
  const MetricChart({
    super.key,
    required this.payload,
    this.incidentStart,
    this.incidentEnd,
  });

  final MetricQueryPayload payload;

  /// When the anomaly began, and when the incident ended (null while open).
  /// The part of the series inside this span is shaded.
  final DateTime? incidentStart;
  final DateTime? incidentEnd;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final points = payload.points;
    final origin = points.first.ts;
    double x(DateTime t) => t.difference(origin).inMilliseconds / 1000;
    final maxX = x(points.last.ts);
    final shaded = _span(x, maxX);
    return SizedBox(
      height: 160,
      child: LineChart(
        LineChartData(
          minX: 0,
          maxX: maxX,
          lineBarsData: [
            LineChartBarData(
              spots: [for (final p in points) FlSpot(x(p.ts), p.value)],
              color: scheme.primary,
              barWidth: 2,
              dotData: const FlDotData(show: false),
            ),
          ],
          rangeAnnotations: RangeAnnotations(
            verticalRangeAnnotations: [
              if (shaded != null)
                VerticalRangeAnnotation(
                  x1: shaded.$1,
                  x2: shaded.$2,
                  color: scheme.error.withValues(alpha: 0.12),
                ),
            ],
          ),
          extraLinesData: ExtraLinesData(
            verticalLines: [
              if (payload.changePointAt case final at?)
                VerticalLine(
                  x: x(at).clamp(0, maxX),
                  color: scheme.error,
                  strokeWidth: 1.5,
                  dashArray: const [4, 4],
                ),
            ],
          ),
          titlesData: FlTitlesData(
            topTitles: const AxisTitles(),
            rightTitles: const AxisTitles(),
            bottomTitles: const AxisTitles(),
            leftTitles: AxisTitles(
              sideTitles: SideTitles(
                showTitles: true,
                reservedSize: 64,
                getTitlesWidget: (value, meta) => SideTitleWidget(
                  meta: meta,
                  child: Text(
                    formatMetric(payload.template, value),
                    style: const TextStyle(fontSize: 10),
                  ),
                ),
              ),
            ),
          ),
          gridData: const FlGridData(drawVerticalLine: false),
          borderData: FlBorderData(show: false),
          lineTouchData: const LineTouchData(enabled: false),
        ),
      ),
    );
  }

  (double, double)? _span(double Function(DateTime) x, double maxX) {
    final start = incidentStart;
    if (start == null) return null;
    final from = x(start).clamp(0.0, maxX);
    final to = incidentEnd == null ? maxX : x(incidentEnd!).clamp(0.0, maxX);
    return to > from ? (from, to) : null;
  }
}
