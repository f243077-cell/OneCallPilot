// MOB-014: the WebSocket client against a fake socket, on a fake clock:
// auth → hello → refetch or resume, ping/pong, drop and reconnect with
// backoff, resync, close codes, token refresh, and the connection status the
// stale-state gate reads (MOB-015).

import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:fake_async/fake_async.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/api/ws_client.dart';
import 'package:oncallpilot/data/models/ws_event.dart';

class FakeSocket implements WsConnection {
  final incoming = StreamController<String>();
  final sent = <Map<String, dynamic>>[];
  int? code;
  bool closedByClient = false;

  @override
  Stream<String> get messages => incoming.stream;

  @override
  void send(String text) => sent.add(jsonDecode(text) as Map<String, dynamic>);

  @override
  Future<void> close([int? code]) async {
    closedByClient = true;
    await incoming.close();
  }

  @override
  int? get closeCode => code;

  void receive(Map<String, dynamic> message) =>
      incoming.add(jsonEncode(message));

  /// The server closes the socket with [closeCode].
  void serverClose([int? closeCode]) {
    code = closeCode;
    unawaited(incoming.close());
  }

  List<String> get types => [for (final m in sent) m['type'] as String];
}

class FakeTokens implements TokenSource {
  String? token = 'token-1';
  bool refreshOk = true;
  int refreshes = 0;
  final changes = StreamController<String>.broadcast();

  @override
  Future<String?> accessToken() async => token;

  @override
  Future<bool> refresh() async {
    refreshes++;
    if (refreshOk) token = 'token-${refreshes + 1}';
    return refreshOk;
  }

  @override
  Stream<String> get refreshed => changes.stream;
}

final t0 = DateTime.utc(2026, 10, 14, 9);

Map<String, dynamic> hello({
  String? latest,
  String serverTime = '2026-10-14T09:00:05Z',
}) => {
  'type': 'hello',
  'server_time': serverTime,
  'contracts_version': '0.1.0',
  'latest_event_id': latest,
};

Map<String, dynamic> event(String id, {int version = 1}) => {
  'type': 'event',
  'event': 'incident.updated',
  'event_id': id,
  'incident_id': '6f1c2a4e-0b7d-4c1e-9a53-2d8e4f6a7b01',
  'state_version': version,
  'ts': '2026-10-14T09:01:05Z',
  'data': {'status': 'executing', 'status_reason': null, 'severity': 'sev2'},
};

class Harness {
  Harness(this.async, {double jitter = 0.5}) {
    client = WsClient(
      url: Uri.parse('ws://192.168.1.20:8000/ws/incidents'),
      connect: (url) async {
        urls.add(url);
        if (failConnects > 0) {
          failConnects--;
          connectTimes.add(async.elapsed);
          throw const SocketFailure();
        }
        final socket = FakeSocket();
        sockets.add(socket);
        connectTimes.add(async.elapsed);
        return socket;
      },
      tokens: tokens,
      refetch: () async {
        refetches++;
        final gate = holdRefetch;
        if (gate != null) await gate.future;
        if (failRefetch) throw const SocketFailure();
      },
      onHello: serverTimes.add,
      onSignedOut: () => signedOut = true,
      clock: () => t0.add(async.elapsed),
      random: _Fixed(jitter),
    );
    client.events.listen(events.add);
    client.statuses.listen(statuses.add);
  }

  final FakeAsync async;
  late final WsClient client;
  final tokens = FakeTokens();
  final sockets = <FakeSocket>[];
  final urls = <Uri>[];
  final connectTimes = <Duration>[];
  final events = <WsEvent>[];
  final statuses = <ConnectionStatus>[];
  final serverTimes = <DateTime>[];
  int refetches = 0;
  int failConnects = 0;
  bool failRefetch = false;
  bool signedOut = false;
  Completer<void>? holdRefetch;

  FakeSocket get socket => sockets.last;

  void run([Duration by = Duration.zero]) {
    async.elapse(by);
    async.flushMicrotasks();
  }

  /// Starts, connects, and completes the first hello and refetch.
  void connectSynced({String? latest = '100-0'}) {
    client.start();
    run();
    socket.receive(hello(latest: latest));
    run();
  }
}

class SocketFailure implements Exception {
  const SocketFailure();
}

/// A Random whose nextDouble is fixed: 0.5 means no jitter.
class _Fixed implements Random {
  _Fixed(this.value);

  final double value;

  @override
  double nextDouble() => value;

  @override
  int nextInt(int max) => 0;

  @override
  bool nextBool() => false;
}

void main() {
  test('connects without a token in the URL and sends auth first', () {
    fakeAsync((async) {
      final h = Harness(async);
      h.client.start();
      h.run();
      expect(h.urls.single.toString(), 'ws://192.168.1.20:8000/ws/incidents');
      expect(h.urls.single.hasQuery, isFalse);
      expect(h.socket.sent, [
        {'type': 'auth', 'token': 'token-1'},
      ]);
      expect(h.client.status, ConnectionStatus.connecting);
    });
  });

  test('first connect: hello sets the clock and refetches, then synced', () {
    fakeAsync((async) {
      final h = Harness(async);
      h.holdRefetch = Completer<void>();
      h.client.start();
      h.run();
      h.socket.receive(hello(latest: '100-0'));
      h.run();
      expect(h.serverTimes, [DateTime.utc(2026, 10, 14, 9, 0, 5)]);
      expect(h.refetches, 1);
      expect(h.client.status, ConnectionStatus.connecting);
      h.holdRefetch!.complete();
      h.run();
      expect(h.client.status, ConnectionStatus.connectedSynced);
      expect(h.client.lastEventId, '100-0');
      expect(h.socket.types, ['auth'], reason: 'no resume on a first connect');
    });
  });

  test('answers every ping with a pong; passes events on in order', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.socket.receive({'type': 'ping'});
      h.socket.receive(event('101-0', version: 2));
      h.socket.receive(event('101-1', version: 3));
      h.socket.receive({'type': 'ping'});
      h.run();
      expect(h.socket.types, ['auth', 'pong', 'pong']);
      expect(h.events.map((e) => e.eventId), ['101-0', '101-1']);
      expect(h.client.lastEventId, '101-1');
    });
  });

  test('a drop disables approval at once, is offline after 3 s, and '
      'reconnects with resume; synced only after catching up', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.socket.receive(event('101-0'));
      h.run();
      h.socket.serverClose();
      h.run();
      expect(h.client.status, ConnectionStatus.connecting);
      h.run(const Duration(milliseconds: 999));
      expect(h.sockets, hasLength(1), reason: 'first retry after 1 s');
      h.run(const Duration(milliseconds: 1));
      expect(h.sockets, hasLength(2));
      final second = h.socket;
      expect(second.types, ['auth']);
      second.receive(hello(latest: '105-0'));
      h.run();
      expect(second.sent.last, {'type': 'resume', 'last_event_id': '101-0'});
      expect(h.refetches, 1, reason: 'resume, not refetch');
      expect(h.client.status, ConnectionStatus.connecting);
      second.receive(event('104-0'));
      h.run();
      expect(h.client.status, ConnectionStatus.connecting);
      second.receive(event('105-0'));
      h.run();
      expect(h.client.status, ConnectionStatus.connectedSynced);
    });
  });

  test('closed for more than 3 s reads offline', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.failConnects = 10;
      h.socket.serverClose();
      h.run(const Duration(seconds: 2));
      expect(h.client.status, ConnectionStatus.connecting);
      h.run(const Duration(seconds: 2));
      expect(h.client.status, ConnectionStatus.offline);
    });
  });

  test('backoff is 1, 2, 4, 8, 16, then 30 s', () {
    fakeAsync((async) {
      final h = Harness(async);
      h.failConnects = 8;
      h.client.start();
      h.run(const Duration(minutes: 3));
      final gaps = [
        for (var i = 1; i < h.connectTimes.length; i++)
          (h.connectTimes[i] - h.connectTimes[i - 1]).inSeconds,
      ];
      expect(gaps, [1, 2, 4, 8, 16, 30, 30, 30]);
    });
  });

  test('jitter stays within ±20 %', () {
    for (final (jitter, millis) in [(0.0, 800), (1.0, 1200)]) {
      fakeAsync((async) {
        final h = Harness(async, jitter: jitter);
        h.failConnects = 1;
        h.client.start();
        h.run(const Duration(seconds: 2));
        expect((h.connectTimes[1] - h.connectTimes[0]).inMilliseconds, millis);
      });
    }
  });

  test('a successful hello resets the backoff', () {
    fakeAsync((async) {
      final h = Harness(async);
      h.failConnects = 3;
      h.client.start();
      h.run(const Duration(seconds: 8)); // 1 + 2 + 4 s of failures
      h.socket.receive(hello(latest: null));
      h.run();
      final before = h.connectTimes.length;
      h.socket.serverClose();
      h.run(const Duration(seconds: 1));
      expect(h.connectTimes.length, before + 1);
    });
  });

  test('resync_required refetches; synced again after', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.socket.receive(event('101-0'));
      h.socket.serverClose();
      h.run(const Duration(seconds: 1));
      h.socket.receive(hello(latest: '900-0'));
      h.socket.receive({'type': 'resync_required'});
      h.run();
      expect(h.refetches, 2);
      expect(h.client.status, ConnectionStatus.connectedSynced);
    });
  });

  test('a failed refetch reconnects and tries again', () {
    fakeAsync((async) {
      final h = Harness(async);
      h.failRefetch = true;
      h.client.start();
      h.run();
      h.socket.receive(hello(latest: '100-0'));
      h.run();
      expect(h.socket.closedByClient, isTrue);
      expect(h.client.status, isNot(ConnectionStatus.connectedSynced));
      h.failRefetch = false;
      h.run(const Duration(seconds: 1));
      h.socket.receive(hello(latest: '100-0'));
      h.run();
      expect(h.client.status, ConnectionStatus.connectedSynced);
    });
  });

  test('4401: refreshes the session, then reconnects with the new token', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.socket.serverClose(4401);
      h.run(const Duration(seconds: 1));
      expect(h.tokens.refreshes, 1);
      expect(h.socket.sent.first, {'type': 'auth', 'token': 'token-2'});
    });
  });

  test('4401 and a failed refresh: signed out, no reconnect', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.tokens.refreshOk = false;
      h.socket.serverClose(4401);
      h.run(const Duration(minutes: 1));
      expect(h.signedOut, isTrue);
      expect(h.sockets, hasLength(1));
      expect(h.client.status, ConnectionStatus.offline);
    });
  });

  test('4408 (no auth in time) reconnects', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.socket.serverClose(4408);
      h.run(const Duration(seconds: 1));
      expect(h.sockets, hasLength(2));
      expect(h.tokens.refreshes, 0);
    });
  });

  test('signed out before connecting: no socket at all', () {
    fakeAsync((async) {
      final h = Harness(async);
      h.tokens.token = null;
      h.client.start();
      h.run(const Duration(seconds: 5));
      expect(h.sockets, isEmpty);
      expect(h.signedOut, isTrue);
    });
  });

  test('a refreshed token is sent as auth on the open socket', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.tokens.changes.add('token-9');
      h.run();
      expect(h.socket.sent.last, {'type': 'auth', 'token': 'token-9'});
      expect(h.sockets, hasLength(1));
    });
  });

  test('no message for 30 s is stale; a ping restores it; 45 s reconnects', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.run(const Duration(seconds: 29));
      expect(h.client.status, ConnectionStatus.connectedSynced);
      h.run(const Duration(seconds: 1));
      expect(h.client.status, ConnectionStatus.stale);
      h.socket.receive({'type': 'ping'});
      h.run();
      expect(h.client.status, ConnectionStatus.connectedSynced);
      h.run(const Duration(seconds: 45));
      expect(h.sockets.first.closedByClient, isTrue);
      h.run(const Duration(seconds: 1));
      expect(h.sockets, hasLength(2));
    });
  });

  test('unknown types and malformed frames are ignored', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      h.socket.incoming.add('not json');
      h.socket.incoming.add('[1, 2]');
      h.socket.receive({'type': 'surprise', 'x': 1});
      h.socket.receive({'type': 'event', 'event_id': '102-0'});
      h.socket.receive(event('103-0'));
      h.run();
      expect(h.events.map((e) => e.eventId), ['103-0']);
      expect(h.client.status, ConnectionStatus.connectedSynced);
    });
  });

  test('stop closes the socket and never reconnects', () {
    fakeAsync((async) {
      final h = Harness(async)..connectSynced();
      unawaited(h.client.stop());
      h.run(const Duration(minutes: 1));
      expect(h.socket.closedByClient, isTrue);
      expect(h.sockets, hasLength(1));
      expect(h.client.status, ConnectionStatus.offline);
    });
  });

  test('connectionStatusProvider follows the socket; synced without one', () {
    fakeAsync((async) {
      final mock = ProviderContainer();
      expect(
        mock.read(connectionStatusProvider),
        ConnectionStatus.connectedSynced,
      );
      mock.dispose();

      final h = Harness(async);
      final live = ProviderContainer(
        overrides: [wsClientProvider.overrideWithValue(h.client)],
      );
      final seen = <ConnectionStatus>[];
      live.listen(connectionStatusProvider, (_, next) => seen.add(next));
      expect(live.read(connectionStatusProvider), ConnectionStatus.offline);
      h.connectSynced();
      expect(
        live.read(connectionStatusProvider),
        ConnectionStatus.connectedSynced,
      );
      h.socket.serverClose();
      h.run();
      expect(live.read(connectionStatusProvider), ConnectionStatus.connecting);
      expect(seen, contains(ConnectionStatus.connectedSynced));
      live.dispose();
    });
  });

  test('event IDs compare by milliseconds, then sequence', () {
    expect(compareEventIds('100-0', '99-5'), greaterThan(0));
    expect(compareEventIds('100-2', '100-10'), lessThan(0));
    expect(compareEventIds('100-2', '100-2'), 0);
  });
}
