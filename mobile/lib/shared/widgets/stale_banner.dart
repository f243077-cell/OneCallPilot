// StaleBanner (MOB-015, architecture §10.4): shown while the view may be out
// of date: the socket is reconnecting, silent for 30 s, or offline; on Action
// Review also while the proposal has expired or a newer version is arriving.
// Approve is disabled for exactly these reasons; Reject stays enabled.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/api/ws_client.dart';

class StaleBanner extends ConsumerWidget {
  const StaleBanner({super.key, this.reason});

  /// A screen's own reason, shown when the connection is fine.
  final String? reason;

  static String? connectionText(ConnectionStatus status) => switch (status) {
    ConnectionStatus.connectedSynced => null,
    ConnectionStatus.connecting => 'Reconnecting — approval is paused',
    ConnectionStatus.stale => 'No updates for 30 s — approval is paused',
    ConnectionStatus.offline => 'Offline — showing the last known state',
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final text = connectionText(ref.watch(connectionStatusProvider)) ?? reason;
    if (text == null) return const SizedBox.shrink();
    final colors = Theme.of(context).colorScheme;
    return Material(
      key: const ValueKey('stale-banner'),
      color: colors.errorContainer,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
        child: Row(
          children: [
            Icon(Icons.sync_problem, color: colors.onErrorContainer),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                text,
                style: TextStyle(color: colors.onErrorContainer),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
