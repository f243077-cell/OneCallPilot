// incidentListProvider of architecture §10.3: the feed from the repository,
// fetched again on every incident event.

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/incident.dart';
import '../../../data/repositories/incident_repository.dart';

final incidentListProvider = FutureProvider<List<IncidentSummary>>((ref) {
  final repository = ref.watch(incidentRepositoryProvider);
  final subscription = repository.events().listen((_) => ref.invalidateSelf());
  ref.onDispose(() => unawaited(subscription.cancel()));
  return repository.listIncidents();
});
