// A2.8 against the real gateway (MOB-014, API-014…API-016): runs only when
// OCP_LIVE_WS_URL is set, so CI (which has no backend) skips it.
//
//   OCP_LIVE_WS_URL    ws://127.0.0.1:8000/ws/incidents
//   OCP_LIVE_TOKEN     a JWT the backend accepts (a test user's, never a real one)
//   OCP_LIVE_SEED      a shell command that seeds one fixture timeline under
//                      fresh IDs (`ocp seed-incident … --new-ids`) and prints
//                      its JSON summary ({"events": N, "last_event_id": …})
//
// It connects (auth → hello → refetch), seeds a timeline and receives every
// event; then stops, seeds again while disconnected, and reconnects: the
// `resume` must deliver exactly the missed events, in order, and end synced.

@Tags(['live'])
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/api/ws_client.dart';
import 'package:oncallpilot/data/models/ws_event.dart';

class _Token implements TokenSource {
  _Token(this.token);

  final String token;

  @override
  Future<String?> accessToken() async => token;

  @override
  Future<bool> refresh() async => false;

  @override
  Stream<String> get refreshed => const Stream.empty();
}

final _env = Platform.environment;

Future<Map<String, dynamic>> _seed() async {
  final result = await Process.run('sh', ['-c', _env['OCP_LIVE_SEED']!]);
  expect(result.exitCode, 0, reason: '${result.stderr}');
  final lines = (result.stdout as String).trim().split('\n');
  return jsonDecode(lines.last) as Map<String, dynamic>;
}

Future<void> _until(bool Function() done, {int seconds = 15}) async {
  final deadline = DateTime.now().add(Duration(seconds: seconds));
  while (!done()) {
    if (DateTime.now().isAfter(deadline)) fail('timed out');
    await Future<void>.delayed(const Duration(milliseconds: 50));
  }
}

void main() {
  final url = _env['OCP_LIVE_WS_URL'];
  test(
    'connect, live events, then resume after a disconnect',
    () async {
      final events = <WsEvent>[];
      var refetches = 0;
      DateTime? serverTime;
      final client = WsClient(
        url: Uri.parse(url!),
        connect: connectWebSocket,
        tokens: _Token(_env['OCP_LIVE_TOKEN']!),
        refetch: () async => refetches++,
        onHello: (t) => serverTime = t,
      );
      addTearDown(client.dispose);
      client.events.listen(events.add);

      client.start();
      await _until(() => client.status == ConnectionStatus.connectedSynced);
      expect(refetches, 1);
      expect(
        serverTime!.difference(DateTime.now().toUtc()).inSeconds.abs(),
        lessThan(5),
      );

      final first = await _seed();
      await _until(() => events.length == first['events']);
      expect(events.last.eventId, first['last_event_id']);
      final ids = events.map((e) => e.eventId).toList();
      expect([...ids]..sort(compareEventIds), ids, reason: 'in order');

      await client.stop();
      events.clear();
      final missed = await _seed(); // published while disconnected
      client.start();
      await _until(() => client.status == ConnectionStatus.connectedSynced);
      await _until(() => events.length == missed['events']);
      expect(refetches, 1, reason: 'a resume, not a refetch');
      expect(events.last.eventId, missed['last_event_id']);
      await Future<void>.delayed(const Duration(seconds: 1));
      expect(events, hasLength(missed['events']), reason: 'no duplicates');
    },
    skip: url == null
        ? 'OCP_LIVE_WS_URL is not set (needs a running backend-api)'
        : false,
    timeout: const Timeout(Duration(minutes: 2)),
  );
}
