// `/incidents/:id` (MOB-004, MOB-005, architecture §10.2): ranked hypotheses
// with confidence bars, categories and `E#` chips that scroll to the matching
// evidence card; dropped hypotheses hidden until asked for. The live log tail
// (MOB-006) comes in task A3.5.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/models/enums.dart';
import '../../../data/models/hypothesis.dart';
import '../../../data/models/incident.dart';
import '../../../shared/formatting.dart';
import '../../../shared/widgets/confidence_bar.dart';
import '../../../shared/widgets/evidence_card.dart';
import '../../../shared/widgets/severity_badge.dart';
import '../../../shared/widgets/time_since.dart';
import '../../incident_feed/presentation/incident_feed_screen.dart';
import '../application/incident_detail_provider.dart';

class IncidentDetailScreen extends ConsumerWidget {
  const IncidentDetailScreen({super.key, required this.incidentId});

  final String incidentId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final provider = incidentDetailProvider(incidentId);
    final detail = ref.watch(provider);
    return Scaffold(
      appBar: AppBar(title: const Text('Incident')),
      body: switch (detail) {
        AsyncValue(:final value?) => IncidentDetailBody(value),
        AsyncError(:final error) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  error is ApiError && error.code == ApiErrorCode.notFound
                      ? 'Incident not found'
                      : describeError(error),
                ),
                const SizedBox(height: 16),
                OutlinedButton(
                  onPressed: () => ref.invalidate(provider),
                  child: const Text('Retry'),
                ),
              ],
            ),
          ),
        ),
        _ => const Center(child: CircularProgressIndicator()),
      },
    );
  }
}

class IncidentDetailBody extends StatefulWidget {
  const IncidentDetailBody(this.incident, {super.key});

  final IncidentDetail incident;

  @override
  State<IncidentDetailBody> createState() => _IncidentDetailBodyState();
}

class _IncidentDetailBodyState extends State<IncidentDetailBody> {
  final _cardKeys = <String, GlobalKey>{};
  bool _showDropped = false;

  GlobalKey _keyFor(String ref) => _cardKeys.putIfAbsent(ref, GlobalKey.new);

  void _scrollTo(String ref) {
    final context = _cardKeys[ref]?.currentContext;
    if (context == null) return;
    Scrollable.ensureVisible(
      context,
      duration: const Duration(milliseconds: 300),
      alignment: 0.05,
    );
  }

  @override
  Widget build(BuildContext context) {
    final incident = widget.incident;
    final theme = Theme.of(context);
    final active =
        incident.hypotheses
            .where((h) => h.status != HypothesisStatus.dropped)
            .toList()
          ..sort((a, b) => a.rank.compareTo(b.rank));
    final dropped = incident.hypotheses
        .where((h) => h.status == HypothesisStatus.dropped)
        .toList();
    final refs = {for (final e in incident.evidence) e.ref};
    final anomalyStart = incident.trigger.signals
        .map((s) => s.firstAnomalousAt)
        .fold<DateTime>(incident.openedAt, (a, b) => b.isBefore(a) ? b : a);

    return SingleChildScrollView(
      padding: const EdgeInsets.only(bottom: 24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(incident.title, style: theme.textTheme.titleLarge),
                const SizedBox(height: 8),
                Row(
                  children: [
                    SeverityBadge(incident.severity),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        '${humanize(incident.service)} · '
                        '${IncidentTile.statusText(incident)}',
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Row(
                  children: [
                    const Text('Opened '),
                    TimeSince(incident.openedAt),
                  ],
                ),
              ],
            ),
          ),
          if (incident.pendingProposalId case final proposalId?)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: FilledButton.icon(
                icon: const Icon(Icons.fact_check_outlined),
                label: const Text('Review proposed action'),
                onPressed: () => context.push('/proposals/$proposalId'),
              ),
            ),
          _Section('Hypotheses'),
          if (active.isEmpty) const _Empty('No hypotheses yet'),
          for (final h in active)
            HypothesisTile(h, knownRefs: refs, onRef: _scrollTo),
          if (dropped.isNotEmpty) ...[
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: TextButton(
                onPressed: () => setState(() => _showDropped = !_showDropped),
                child: Text(
                  _showDropped
                      ? 'Hide dropped hypotheses'
                      : 'Show ${dropped.length} dropped',
                ),
              ),
            ),
            if (_showDropped)
              for (final h in dropped)
                HypothesisTile(h, knownRefs: refs, onRef: _scrollTo),
          ],
          _Section('Evidence (${incident.evidence.length})'),
          if (incident.evidence.isEmpty) const _Empty('No evidence yet'),
          for (final e in incident.evidence)
            KeyedSubtree(
              key: _keyFor(e.ref),
              child: EvidenceCard(
                key: ValueKey('evidence-${e.ref}'),
                evidence: e,
                incidentStart: anomalyStart,
                incidentEnd: incident.windowEnd ?? incident.resolvedAt,
              ),
            ),
        ],
      ),
    );
  }
}

/// One ranked hypothesis: rank, summary, category, confidence and `E#` chips.
class HypothesisTile extends StatelessWidget {
  const HypothesisTile(
    this.hypothesis, {
    super.key,
    required this.knownRefs,
    required this.onRef,
  });

  final Hypothesis hypothesis;
  final Set<String> knownRefs;
  final void Function(String ref) onRef;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final h = hypothesis;
    final dropped = h.status == HypothesisStatus.dropped;
    return Card(
      key: ValueKey('hypothesis-${h.id}'),
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('#${h.rank}', style: theme.textTheme.titleMedium),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    humanize(h.rootCauseCategory),
                    style: theme.textTheme.labelLarge,
                  ),
                ),
                if (dropped) Text('Dropped', style: theme.textTheme.labelSmall),
              ],
            ),
            const SizedBox(height: 4),
            Text(h.summary),
            const SizedBox(height: 8),
            ConfidenceBar(h.confidence),
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              children: [
                for (final ref in h.evidenceRefs)
                  ActionChip(
                    key: ValueKey('chip-${h.id}-$ref'),
                    label: Text(ref),
                    onPressed: knownRefs.contains(ref)
                        ? () => onRef(ref)
                        : null,
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section(this.title);

  final String title;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 16, 16, 4),
    child: Text(title, style: Theme.of(context).textTheme.titleMedium),
  );
}

class _Empty extends StatelessWidget {
  const _Empty(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
    child: Text(text),
  );
}
