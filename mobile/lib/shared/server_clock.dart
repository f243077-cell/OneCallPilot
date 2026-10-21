// serverClockOffsetProvider of architecture §10.3 (NFR-014): server time minus
// phone time, from `hello.server_time` once the WebSocket client (task A2.8)
// connects; zero until then and in mock mode. Expiry countdowns use server time
// so a phone with a wrong clock never shows a proposal as approvable too long.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'formatting.dart';

class ServerClockOffset extends Notifier<Duration> {
  @override
  Duration build() => Duration.zero;

  /// From a `hello` message: its `server_time` against the phone's clock.
  void sync(DateTime serverTime) {
    state = serverTime.toUtc().difference(ref.read(clockProvider)().toUtc());
  }
}

final serverClockOffsetProvider = NotifierProvider<ServerClockOffset, Duration>(
  ServerClockOffset.new,
);

/// The server's current time, as best the phone knows it.
final serverNowProvider = Provider<DateTime Function()>((ref) {
  final clock = ref.watch(clockProvider);
  final offset = ref.watch(serverClockOffsetProvider);
  return () => clock().toUtc().add(offset);
});
