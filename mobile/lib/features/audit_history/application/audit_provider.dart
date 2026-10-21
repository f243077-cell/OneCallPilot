// The audit history (MOB-012, API-012): all entries, or one incident's.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/models/ops.dart';
import '../../../data/repositories/incident_repository.dart';

/// Keyed by incident ID; null means every incident.
final auditProvider = FutureProvider.autoDispose
    .family<List<AuditEntry>, String?>(
      (ref, incidentId) => ref
          .watch(incidentRepositoryProvider)
          .listAudit(incidentId: incidentId),
      retry: (_, _) => null,
    );
