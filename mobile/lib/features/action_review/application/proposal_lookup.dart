// The incident that holds a proposal, for `/proposals/:id` opened without
// `?incident=` (the API has no `GET /proposals/{id}`): the feed's pending
// proposals first, then each incident's detail.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/repositories/incident_repository.dart';

final proposalIncidentProvider = FutureProvider.family<String, String>((
  ref,
  proposalId,
) async {
  final repository = ref.watch(incidentRepositoryProvider);
  final incidents = await repository.listIncidents();
  for (final incident in incidents) {
    if (incident.pendingProposalId == proposalId) return incident.id;
  }
  for (final incident in incidents) {
    final detail = await repository.getIncident(incident.id);
    if (detail.proposals.any((p) => p.id == proposalId)) return incident.id;
  }
  throw const ApiError(
    ApiErrorCode.notFound,
    status: 404,
    message: 'No such proposal',
  );
}, retry: (_, _) => null);
