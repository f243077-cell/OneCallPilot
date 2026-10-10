// incidentDetailProvider(id) of architecture §10.3: one incident, fetched
// again when an event with a newer state_version arrives for it.

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/incident.dart';
import '../../../data/repositories/incident_repository.dart';

class IncidentDetailNotifier extends AsyncNotifier<IncidentDetail> {
  IncidentDetailNotifier(this.incidentId);

  final String incidentId;

  @override
  Future<IncidentDetail> build() async {
    final repository = ref.watch(incidentRepositoryProvider);
    final subscription = repository.events().listen((event) {
      final shown = state.value?.stateVersion ?? 0;
      if (event.incidentId == incidentId && event.stateVersion > shown) {
        unawaited(refresh());
      }
    });
    ref.onDispose(() => unawaited(subscription.cancel()));
    return repository.getIncident(incidentId);
  }

  /// Fetches the incident again, keeping the current one on screen meanwhile.
  Future<void> refresh() async {
    final result = await AsyncValue.guard(
      () => ref.read(incidentRepositoryProvider).getIncident(incidentId),
    );
    if (ref.mounted) state = result;
  }
}

final incidentDetailProvider =
    AsyncNotifierProvider.family<
      IncidentDetailNotifier,
      IncidentDetail,
      String
    >(IncidentDetailNotifier.new);
