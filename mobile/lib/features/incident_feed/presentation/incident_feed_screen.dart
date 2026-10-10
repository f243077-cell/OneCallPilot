// `/incidents` (MOB-003, architecture §10.2): severity, service, status and a
// live "time since alert", in open and past tabs. New incidents appear without
// a manual refresh; pull down to refetch.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/models/enums.dart';
import '../../../data/models/incident.dart';
import '../../../shared/formatting.dart';
import '../../../shared/widgets/severity_badge.dart';
import '../../../shared/widgets/time_since.dart';
import '../application/incident_list_provider.dart';

class IncidentFeedScreen extends ConsumerWidget {
  const IncidentFeedScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final incidents = ref.watch(incidentListProvider);
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Incidents'),
          actions: [
            IconButton(
              tooltip: 'Settings',
              icon: const Icon(Icons.settings_outlined),
              onPressed: () => context.push('/settings'),
            ),
          ],
          bottom: const TabBar(
            tabs: [
              Tab(text: 'Open'),
              Tab(text: 'Past'),
            ],
          ),
        ),
        body: switch (incidents) {
          AsyncValue(:final value?) => TabBarView(
            children: [
              _IncidentList(
                value
                    .where((i) => i.status != IncidentStatus.resolved)
                    .toList(),
                empty: 'No open incidents',
              ),
              _IncidentList(
                value
                    .where((i) => i.status == IncidentStatus.resolved)
                    .toList(),
                empty: 'No past incidents',
              ),
            ],
          ),
          AsyncError(:final error) => _ErrorState(
            error,
            onRetry: () => ref.invalidate(incidentListProvider),
          ),
          _ => const Center(child: CircularProgressIndicator()),
        },
      ),
    );
  }
}

class _IncidentList extends ConsumerWidget {
  const _IncidentList(this.items, {required this.empty});

  final List<IncidentSummary> items;
  final String empty;

  @override
  Widget build(BuildContext context, WidgetRef ref) => RefreshIndicator(
    onRefresh: () => ref.read(incidentListProvider.notifier).refresh(),
    child: items.isEmpty
        ? ListView(
            children: [
              const SizedBox(height: 120),
              Center(child: Text(empty)),
            ],
          )
        : ListView.separated(
            itemCount: items.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (context, index) => IncidentTile(items[index]),
          ),
  );
}

/// One incident in the feed.
class IncidentTile extends StatelessWidget {
  const IncidentTile(this.incident, {super.key});

  final IncidentSummary incident;

  static String statusText(IncidentSummary i) {
    final status = humanize(i.status);
    return switch (i.status) {
      IncidentStatus.escalated when i.statusReason != null =>
        '$status: ${humanize(i.statusReason!).toLowerCase()}',
      IncidentStatus.resolved when i.resolution != null =>
        '$status: ${humanize(i.resolution!).toLowerCase()}',
      _ => status,
    };
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return ListTile(
      key: ValueKey('incident-${incident.id}'),
      onTap: () => context.push('/incidents/${incident.id}'),
      title: Row(
        children: [
          SeverityBadge(incident.severity),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              incident.title,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Row(
          children: [
            Expanded(
              child: Text(
                '${humanize(incident.service)} · ${statusText(incident)}',
              ),
            ),
            TimeSince(incident.openedAt, style: theme.textTheme.bodySmall),
          ],
        ),
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState(this.error, {required this.onRetry});

  final Object error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text('Cannot load incidents'),
          const SizedBox(height: 8),
          Text(describeError(error), textAlign: TextAlign.center),
          const SizedBox(height: 16),
          OutlinedButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    ),
  );
}
