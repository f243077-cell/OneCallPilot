// `/incidents`: a plain list until task A1.6 builds the real feed (severity,
// status, time since alert, open and past tabs).

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../application/incident_list_provider.dart';

class IncidentFeedScreen extends ConsumerWidget {
  const IncidentFeedScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final incidents = ref.watch(incidentListProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Incidents'),
        actions: [
          IconButton(
            tooltip: 'Settings',
            icon: const Icon(Icons.settings_outlined),
            onPressed: () => context.push('/settings'),
          ),
        ],
      ),
      body: switch (incidents) {
        AsyncData(:final value) when value.isEmpty => const Center(
          child: Text('No incidents yet'),
        ),
        AsyncData(:final value) => ListView(
          children: [
            for (final incident in value)
              ListTile(
                title: Text(incident.title),
                subtitle: Text(
                  '${incident.service.name} · ${incident.status.name}',
                ),
                onTap: () => context.push('/incidents/${incident.id}'),
              ),
          ],
        ),
        AsyncError(:final error) => Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Text('Cannot load incidents: $error'),
          ),
        ),
        _ => const Center(child: CircularProgressIndicator()),
      },
    );
  }
}
