// The metric evidence chart (MOB-005): the series as a line, the incident's
// span shaded, and the change point marked with a dashed line.

import 'dart:math';

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
    final axis = YAxis.forValues(points.map((p) => p.value));
    // Vertical padding keeps the top and bottom labels clear of the text
    // above and below the chart.
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: SizedBox(
        height: 160,
        child: LineChart(
          LineChartData(
            minX: 0,
            maxX: maxX,
            minY: axis.min,
            maxY: axis.max,
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
                  interval: axis.interval,
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
            gridData: FlGridData(
              drawVerticalLine: false,
              horizontalInterval: axis.interval,
            ),
            borderData: FlBorderData(show: false),
            lineTouchData: const LineTouchData(enabled: false),
          ),
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

/// The y axis: from 0 (metrics are never negative) to a multiple of a
/// "nice" interval (1, 2, 2.5 or 5 times a power of ten) at or above the
/// highest value, so that fl_chart's labels at the axis ends land on grid
/// lines instead of crowding the nearest one.
class YAxis {
  const YAxis(this.min, this.max, this.interval);

  factory YAxis.forValues(Iterable<double> values, {int ticks = 4}) {
    final top = values.fold<double>(0, (a, b) => b > a ? b : a);
    if (top <= 0) return const YAxis(0, 1, 0.25);
    final interval = niceStep(top / ticks);
    // Rounded, so 3 x 0.2 is 0.6 and not 0.6000000000000001.
    final max = double.parse(
      ((top / interval).ceil() * interval).toStringAsPrecision(12),
    );
    return YAxis(0, max, interval);
  }

  final double min;
  final double max;
  final double interval;

  static double niceStep(double raw) {
    final magnitude = pow(10, (log(raw) / ln10).floor()).toDouble();
    final fraction = raw / magnitude;
    final nice = fraction <= 1
        ? 1.0
        : fraction <= 2
        ? 2.0
        : fraction <= 2.5
        ? 2.5
        : fraction <= 5
        ? 5.0
        : 10.0;
    return nice * magnitude;
  }
}
