// Monitor settings (MOB-013, API-013): read, then saved with the version they
// were read at (FR-023). A conflict or a benchmark lock reloads them.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/models/ops.dart';
import '../../../data/repositories/incident_repository.dart';

class MonitorSettingsNotifier extends AsyncNotifier<MonitorSettings> {
  @override
  Future<MonitorSettings> build() =>
      ref.watch(incidentRepositoryProvider).getSettings();

  /// Saves [update]; returns null on success, or the message to show. A
  /// VERSION_CONFLICT or BENCHMARK_LOCKED reloads the current settings.
  Future<String?> save(MonitorSettingsUpdate update) async {
    try {
      final saved = await ref
          .read(incidentRepositoryProvider)
          .updateSettings(update);
      state = AsyncData(saved);
      return null;
    } on ApiError catch (e) {
      if (e.code == ApiErrorCode.versionConflict ||
          e.code == ApiErrorCode.benchmarkLocked) {
        ref.invalidateSelf();
        return e.code == ApiErrorCode.versionConflict
            ? 'Settings were changed elsewhere — reloaded; check and save again'
            : 'Settings are locked: ${e.message}';
      }
      return e.userMessage;
    } on NetworkError catch (e) {
      return describeError(e);
    }
  }
}

final monitorSettingsProvider =
    AsyncNotifierProvider.autoDispose<MonitorSettingsNotifier, MonitorSettings>(
      MonitorSettingsNotifier.new,
      retry: (_, _) => null,
    );
