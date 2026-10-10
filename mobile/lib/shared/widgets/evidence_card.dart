// EvidenceCard (MOB-005, architecture §10.1): one variant per evidence kind.
// Log: lines with errors highlighted. Metric: fl_chart line. Commit: release,
// SHA, message, time. Health: container states. Runbook: heading and snippet.
// A kind this build does not know, or a payload of the wrong shape, shows the
// summary with a short note instead of failing.

import 'package:flutter/material.dart';

import '../../data/models/enums.dart';
import '../../data/models/evidence.dart';
import '../../data/models/evidence_payloads.dart';
import '../formatting.dart';
import 'metric_chart.dart';

class EvidenceCard extends StatelessWidget {
  const EvidenceCard({
    super.key,
    required this.evidence,
    this.incidentStart,
    this.incidentEnd,
  });

  final Evidence evidence;

  /// The incident's span, shaded on metric charts.
  final DateTime? incidentStart;
  final DateTime? incidentEnd;

  static String kindLabel(EvidenceKind kind) => switch (kind) {
    EvidenceKind.detectorSignal => 'Alert signal',
    EvidenceKind.logQuery => 'Logs',
    EvidenceKind.metricQuery => 'Metric',
    EvidenceKind.deployList => 'Deploys',
    EvidenceKind.serviceHealth => 'Health',
    EvidenceKind.runbookHit => 'Runbook',
    EvidenceKind.unknown => 'Unknown evidence',
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final payload = parsePayload(evidence);
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Chip(
                  label: Text(evidence.ref),
                  visualDensity: VisualDensity.compact,
                ),
                const SizedBox(width: 8),
                Text(
                  kindLabel(evidence.kind),
                  style: theme.textTheme.titleSmall,
                ),
                const Spacer(),
                if (evidence.purpose == EvidencePurpose.disproof)
                  Text('disproof check', style: theme.textTheme.labelSmall),
              ],
            ),
            const SizedBox(height: 4),
            Text(evidence.summary),
            const SizedBox(height: 8),
            switch (payload) {
              DetectorSignalPayload() => _Signals(payload),
              LogQueryPayload() => _Logs(payload),
              MetricQueryPayload() => _Metric(
                payload,
                incidentStart: incidentStart,
                incidentEnd: incidentEnd,
              ),
              DeployListPayload() => _Deploys(payload),
              ServiceHealthPayload() => _Health(payload),
              RunbookHitPayload() => _Runbook(payload),
              null => Text(
                'This app version cannot show this evidence type.',
                style: theme.textTheme.bodySmall,
              ),
            },
          ],
        ),
      ),
    );
  }
}

class _Signals extends StatelessWidget {
  const _Signals(this.payload);

  final DetectorSignalPayload payload;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      for (final s in payload.signals)
        Text(
          '${humanize(s.name)}: ${s.value}'
          '${s.baseline == null ? '' : ' (baseline ${s.baseline})'}'
          '${s.zscore == null ? '' : ', z ${s.zscore}'}',
        ),
    ],
  );
}

class _Logs extends StatelessWidget {
  const _Logs(this.payload);

  final LogQueryPayload payload;

  static bool isError(LogLevel level) =>
      level == LogLevel.error || level == LogLevel.critical;

  @override
  Widget build(BuildContext context) {
    if (payload.lines.isEmpty) {
      return Text('No matching lines (${payload.totalCount} in the window)');
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('${payload.lines.length} of ${payload.totalCount} matching lines'),
        const SizedBox(height: 4),
        for (final line in payload.lines)
          LogLineRow(line, highlighted: isError(line.level)),
        if (payload.topExceptionTypes.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(
            payload.topExceptionTypes
                .map((e) => '${e.excType} ×${e.count}')
                .join(', '),
          ),
        ],
      ],
    );
  }
}

/// One log line; ERROR and CRITICAL lines are highlighted (MOB-005).
class LogLineRow extends StatelessWidget {
  const LogLineRow(this.line, {super.key, required this.highlighted});

  final LogLine line;
  final bool highlighted;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final mono = Theme.of(
      context,
    ).textTheme.bodySmall?.copyWith(fontFamily: 'monospace');
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 2),
      padding: const EdgeInsets.all(4),
      color: highlighted ? scheme.errorContainer : null,
      child: Text(
        '${formatClock(line.ts)} ${line.level.name.toUpperCase()} ${line.msg}',
        style: mono?.copyWith(
          color: highlighted ? scheme.onErrorContainer : null,
        ),
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric(this.payload, {this.incidentStart, this.incidentEnd});

  final MetricQueryPayload payload;
  final DateTime? incidentStart;
  final DateTime? incidentEnd;

  @override
  Widget build(BuildContext context) {
    final p = payload;
    String value(double? v) => v == null ? '-' : formatMetric(p.template, v);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('${metricLabel(p.template)} · ${humanize(p.service)}'),
        const SizedBox(height: 8),
        if (p.points.length >= 2)
          MetricChart(
            payload: p,
            incidentStart: incidentStart,
            incidentEnd: incidentEnd,
          )
        else
          Text(p.note ?? 'No data in the window'),
        const SizedBox(height: 4),
        Text(
          'Baseline ${value(p.baselineMean)} · peak ${value(p.peak)}'
          '${p.pctChange == null ? '' : ' · ${p.pctChange! >= 0 ? '+' : ''}${p.pctChange!.round()} %'}'
          '${p.changePointAt == null ? '' : ' · changed at ${formatClock(p.changePointAt!)}'}',
        ),
      ],
    );
  }
}

class _Deploys extends StatelessWidget {
  const _Deploys(this.payload);

  final DeployListPayload payload;

  static String _short(String sha) =>
      sha.length > 12 ? sha.substring(0, 12) : sha;

  @override
  Widget build(BuildContext context) {
    if (payload.records.isEmpty) return const Text('No recent deploys');
    final mono = Theme.of(
      context,
    ).textTheme.bodySmall?.copyWith(fontFamily: 'monospace');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final r in payload.records)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${humanize(r.service)} ${r.release} · ${humanize(r.kind)} '
                  'by ${humanize(r.deployedBy)}',
                ),
                Text(r.commitMessage),
                Text(
                  '${_short(r.commitSha)} · ${formatDateTime(r.deployedAt)}',
                  style: mono,
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _Health extends StatelessWidget {
  const _Health(this.payload);

  final ServiceHealthPayload payload;

  @override
  Widget build(BuildContext context) {
    final probe = payload.probe;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final c in payload.containers)
          Text(
            '${c.name} (${c.release}): ${humanize(c.state)}, '
            '${humanize(c.health)}, ${c.restartCount} restarts'
            '${c.oomKilled ? ', killed for memory' : ''}',
          ),
        if (payload.containers.isEmpty) const Text('No containers found'),
        if (probe != null)
          Text(
            probe.ok
                ? 'Health probe OK (${probe.statusCode}, '
                      '${probe.latencyMs?.round() ?? '-'} ms)'
                : 'Health probe failed: ${probe.error ?? probe.statusCode}',
          ),
      ],
    );
  }
}

class _Runbook extends StatelessWidget {
  const _Runbook(this.payload);

  final RunbookHitPayload payload;

  static const snippetLength = 280;

  @override
  Widget build(BuildContext context) {
    if (payload.chunks.isEmpty) return const Text('No matching runbook');
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final chunk in payload.chunks) ...[
          Text(
            chunk.heading ?? chunk.source,
            style: theme.textTheme.titleSmall,
          ),
          Text(chunk.source, style: theme.textTheme.labelSmall),
          Text(
            chunk.content.length > snippetLength
                ? '${chunk.content.substring(0, snippetLength)}…'
                : chunk.content,
          ),
          const SizedBox(height: 6),
        ],
      ],
    );
  }
}
