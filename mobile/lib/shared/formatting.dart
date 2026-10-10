// Text for enum values, ages and times. An `unknown` enum value (a value this
// build does not know, MOB-020) always reads "Unknown", never crashes.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/models/enums.dart';

/// The time source of every "time since" label; tests override it.
final clockProvider = Provider<DateTime Function()>((ref) => DateTime.now);

/// `awaitingApproval` → "Awaiting approval"; `unknown` → "Unknown".
String humanize(Enum value) {
  final words = value.name.replaceAllMapped(
    RegExp('[A-Z]'),
    (m) => ' ${m[0]!.toLowerCase()}',
  );
  return words[0].toUpperCase() + words.substring(1);
}

String severityLabel(Severity severity) => switch (severity) {
  Severity.unknown => 'SEV?',
  _ => severity.name.toUpperCase(),
};

String metricLabel(MetricTemplate template) => switch (template) {
  MetricTemplate.p95Latency => 'p95 latency',
  MetricTemplate.memoryRss => 'Memory (RSS)',
  MetricTemplate.cpuUsage => 'CPU usage',
  MetricTemplate.dbPoolInUse => 'DB pool in use',
  MetricTemplate.dbPoolWaitP95 => 'DB pool wait p95',
  MetricTemplate.upstreamLatencyP95 => 'Upstream latency p95',
  MetricTemplate.jobLatencyP95 => 'Job latency p95',
  MetricTemplate.unknown => 'Unknown metric',
  _ => humanize(template),
};

/// A metric value with the unit its template implies. C1 carries no unit, so
/// this follows the metric templates of architecture §6.3.
String formatMetric(MetricTemplate template, double value) =>
    switch (template) {
      MetricTemplate.errorRate ||
      MetricTemplate.jobFailureRate => '${_trim(value * 100)} %',
      MetricTemplate.cpuUsage => '${_trim(value)} cores',
      MetricTemplate.p95Latency ||
      MetricTemplate.upstreamLatencyP95 ||
      MetricTemplate.dbPoolWaitP95 ||
      MetricTemplate.jobLatencyP95 =>
        value < 1 ? '${_trim(value * 1000)} ms' : '${_trim(value)} s',
      MetricTemplate.memoryRss => '${_trim(value / (1 << 20))} MiB',
      MetricTemplate.requestRate => '${_trim(value)} rps',
      _ => _trim(value),
    };

String _trim(double value) => value.abs() >= 100
    ? value.toStringAsFixed(0)
    : value.abs() >= 10
    ? value.toStringAsFixed(1)
    : value.toStringAsFixed(2);

/// "just now", "45 s", "12 min", "3 h", "2 d".
String formatAge(Duration age) {
  if (age.inSeconds < 5) return 'just now';
  if (age.inMinutes < 1) return '${age.inSeconds} s';
  if (age.inHours < 1) return '${age.inMinutes} min';
  if (age.inDays < 1) return '${age.inHours} h';
  return '${age.inDays} d';
}

/// Local wall-clock time, `HH:mm:ss`.
String formatClock(DateTime time) {
  final local = time.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(local.hour)}:${two(local.minute)}:${two(local.second)}';
}

/// Local date and time, `yyyy-MM-dd HH:mm`.
String formatDateTime(DateTime time) {
  final local = time.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)} '
      '${two(local.hour)}:${two(local.minute)}';
}
