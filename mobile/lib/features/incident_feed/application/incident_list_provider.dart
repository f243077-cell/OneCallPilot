// incidentListProvider of architecture §10.3: the feed from the repository,
// kept up to date from incident events and de-duplicated by state_version.
// `incident.opened` is applied from the event itself, so a new incident is on
// screen in the next frame (MOB-003); other events refetch the list.

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/enums.dart';
import '../../../data/models/incident.dart';
import '../../../data/models/ws_event.dart';
import '../../../data/repositories/incident_repository.dart';

class IncidentListNotifier extends AsyncNotifier<List<IncidentSummary>> {
  final _versions = <String, int>{};

  @override
  Future<List<IncidentSummary>> build() async {
    final repository = ref.watch(incidentRepositoryProvider);
    final subscription = repository.events().listen(_onEvent);
    ref.onDispose(() => unawaited(subscription.cancel()));
    return _remember(await repository.listIncidents());
  }

  List<IncidentSummary> _remember(List<IncidentSummary> items) {
    for (final item in items) {
      _versions[item.id] = item.stateVersion;
    }
    return items;
  }

  void _onEvent(WsEvent event) {
    final known = _versions[event.incidentId];
    if (known != null && event.stateVersion <= known) return; // already seen
    final current = state.value;
    if (event.event == WsEventName.incidentOpened && current != null) {
      final opened = IncidentSummary.fromJson(event.data);
      _versions[opened.id] = opened.stateVersion;
      state = AsyncData([opened, ...current.where((i) => i.id != opened.id)]);
      return;
    }
    unawaited(refresh());
  }

  /// Fetches the list again, keeping the current one on screen meanwhile.
  Future<void> refresh() async {
    final result = await AsyncValue.guard(
      () => ref.read(incidentRepositoryProvider).listIncidents(),
    );
    if (!ref.mounted) return;
    state = result.whenData(_remember);
  }
}

final incidentListProvider =
    AsyncNotifierProvider<IncidentListNotifier, List<IncidentSummary>>(
      IncidentListNotifier.new,
    );
