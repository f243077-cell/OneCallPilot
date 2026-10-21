// `/settings` (MOB-013, architecture §10.2): monitored services and detector
// thresholds (editable unless the benchmark lock is on, then read-only with the
// reason), the push severity, the server URL (debug builds only), the
// signed-in user and sign-out.

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/config/env.dart';
import '../../../core/errors/api_error.dart';
import '../../../data/models/enums.dart';
import '../../../data/models/ops.dart';
import '../../../shared/formatting.dart';
import '../../auth/application/auth_session.dart';
import '../application/settings_controller.dart';

/// Services the detector can watch (C1 ServiceName).
const monitorable = [
  ServiceName.api,
  ServiceName.worker,
  ServiceName.lb,
  ServiceName.payments,
  ServiceName.redis,
  ServiceName.postgres,
];

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key, this.debugBuild = kDebugMode});

  /// Shows the server URL; tests set it.
  final bool debugBuild;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final env = ref.watch(envProvider);
    final user = ref.watch(authStateProvider).user;
    final settings = ref.watch(monitorSettingsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        children: [
          switch (settings) {
            AsyncData(:final value) => MonitorSettingsForm(
              key: ValueKey('settings-v${value.version}'),
              settings: value,
            ),
            AsyncError(:final error) => ListTile(
              title: const Text('Monitor settings'),
              subtitle: Text(describeError(error)),
              trailing: TextButton(
                onPressed: () => ref.invalidate(monitorSettingsProvider),
                child: const Text('Retry'),
              ),
            ),
            _ => const Padding(
              padding: EdgeInsets.all(24),
              child: Center(child: CircularProgressIndicator()),
            ),
          },
          const Divider(),
          ListTile(
            title: const Text('Signed in as'),
            subtitle: Text(user?.email ?? '-'),
          ),
          ListTile(
            title: const Text('Data source'),
            subtitle: Text(env.dataSource.name),
          ),
          if (debugBuild && !env.isMock)
            ListTile(
              key: const ValueKey('server-url'),
              title: const Text('Server (debug build)'),
              subtitle: Text('${env.apiBaseUrl}\n${env.wsUrl}'),
              isThreeLine: true,
            ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.logout),
            title: const Text('Sign out'),
            onTap: () => ref.read(authRepositoryProvider).signOut(),
          ),
        ],
      ),
    );
  }
}

class MonitorSettingsForm extends ConsumerStatefulWidget {
  const MonitorSettingsForm({super.key, required this.settings});

  final MonitorSettings settings;

  @override
  ConsumerState<MonitorSettingsForm> createState() =>
      _MonitorSettingsFormState();
}

class _MonitorSettingsFormState extends ConsumerState<MonitorSettingsForm> {
  late Set<ServiceName> _services = widget.settings.services.toSet();
  late Severity _severity = widget.settings.notify.minPushSeverity;
  late final Map<String, TextEditingController> _thresholds = {
    for (final MapEntry(:key, :value) in widget.settings.thresholds.entries)
      key: TextEditingController(text: _number(value)),
  };
  bool _saving = false;
  String? _message;

  static String _number(double v) =>
      v == v.roundToDouble() ? v.toStringAsFixed(0) : '$v';

  @override
  void dispose() {
    for (final c in _thresholds.values) {
      c.dispose();
    }
    super.dispose();
  }

  Map<String, double>? _parsedThresholds() {
    final out = <String, double>{};
    for (final MapEntry(:key, :value) in _thresholds.entries) {
      final number = double.tryParse(value.text.trim());
      if (number == null || number < 0) return null;
      out[key] = number;
    }
    return out;
  }

  Future<void> _save() async {
    final thresholds = _parsedThresholds();
    if (thresholds == null) {
      setState(() => _message = 'Thresholds must be numbers of 0 or more');
      return;
    }
    if (_services.isEmpty) {
      setState(() => _message = 'Watch at least one service');
      return;
    }
    // A save or a reload replaces this form (it is keyed by version), so the
    // outcome is told by a snack bar that outlives it.
    final messenger = ScaffoldMessenger.of(context);
    setState(() {
      _saving = true;
      _message = null;
    });
    final message = await ref
        .read(monitorSettingsProvider.notifier)
        .save(
          MonitorSettingsUpdate(
            services: [
              for (final s in monitorable)
                if (_services.contains(s)) s,
            ],
            thresholds: thresholds,
            notify: NotifySettings(minPushSeverity: _severity),
            version: widget.settings.version,
          ),
        );
    messenger.showSnackBar(
      SnackBar(content: Text(message ?? 'Settings saved')),
    );
    if (mounted) setState(() => _saving = false);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final s = widget.settings;
    final editable = !s.locked && !_saving;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (s.locked)
            Card(
              key: const ValueKey('settings-locked'),
              color: theme.colorScheme.secondaryContainer,
              child: ListTile(
                leading: const Icon(Icons.lock_outline),
                title: const Text('Read-only'),
                subtitle: Text(s.lockReason ?? 'Settings are locked'),
              ),
            ),
          Text('Monitored services', style: theme.textTheme.titleMedium),
          const SizedBox(height: 6),
          Wrap(
            spacing: 6,
            children: [
              for (final service in monitorable)
                FilterChip(
                  key: ValueKey('service-${service.name}'),
                  label: Text(humanize(service)),
                  selected: _services.contains(service),
                  onSelected: editable
                      ? (on) => setState(() {
                          _services = {..._services};
                          on
                              ? _services.add(service)
                              : _services.remove(service);
                        })
                      : null,
                ),
            ],
          ),
          const SizedBox(height: 16),
          Text('Detector thresholds', style: theme.textTheme.titleMedium),
          if (_thresholds.isEmpty)
            const Text('Defaults from the detector configuration'),
          for (final MapEntry(:key, :value) in _thresholds.entries)
            TextField(
              key: ValueKey('threshold-$key'),
              controller: value,
              enabled: editable,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              decoration: InputDecoration(labelText: key),
            ),
          const SizedBox(height: 16),
          Text('Push notifications from', style: theme.textTheme.titleMedium),
          const SizedBox(height: 6),
          SegmentedButton<Severity>(
            key: const ValueKey('min-severity'),
            segments: [
              for (final level in [Severity.sev1, Severity.sev2, Severity.sev3])
                ButtonSegment(value: level, label: Text(severityLabel(level))),
            ],
            selected: {_severity},
            onSelectionChanged: editable
                ? (picked) => setState(() => _severity = picked.single)
                : null,
          ),
          const SizedBox(height: 8),
          Text(
            'Version ${s.version} · changed ${formatDateTime(s.updatedAt)}',
            style: theme.textTheme.bodySmall,
          ),
          if (_message case final message?)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(message, key: const ValueKey('settings-message')),
            ),
          if (!s.locked)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: FilledButton(
                key: const ValueKey('save-settings'),
                onPressed: editable ? _save : null,
                child: const Text('Save'),
              ),
            ),
        ],
      ),
    );
  }
}
