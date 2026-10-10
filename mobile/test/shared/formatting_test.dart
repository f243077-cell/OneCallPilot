import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/models/enums.dart';
import 'package:oncallpilot/shared/formatting.dart';

void main() {
  test('enum names read as words; unknown reads Unknown', () {
    expect(humanize(IncidentStatus.awaitingApproval), 'Awaiting approval');
    expect(humanize(SignalName.p95Latency), 'P95 latency');
    expect(humanize(RootCauseCategory.unknown), 'Unknown');
    expect(severityLabel(Severity.sev2), 'SEV2');
    expect(severityLabel(Severity.unknown), 'SEV?');
    expect(metricLabel(MetricTemplate.unknown), 'Unknown metric');
  });

  test('ages', () {
    expect(formatAge(const Duration(seconds: 2)), 'just now');
    expect(formatAge(const Duration(seconds: 45)), '45 s');
    expect(formatAge(const Duration(minutes: 12, seconds: 59)), '12 min');
    expect(formatAge(const Duration(hours: 3, minutes: 5)), '3 h');
    expect(formatAge(const Duration(days: 2, hours: 1)), '2 d');
  });

  test('metric values carry the unit of their template', () {
    expect(formatMetric(MetricTemplate.errorRate, 0.31), '31.0 %');
    expect(formatMetric(MetricTemplate.p95Latency, 0.12), '120 ms');
    expect(formatMetric(MetricTemplate.p95Latency, 2.9), '2.90 s');
    expect(formatMetric(MetricTemplate.cpuUsage, 0.5), '0.50 cores');
    expect(formatMetric(MetricTemplate.requestRate, 38), '38.0 rps');
    expect(formatMetric(MetricTemplate.memoryRss, 230 * 1048576), '230 MiB');
    expect(formatMetric(MetricTemplate.unknown, 7), '7.00');
  });
}
