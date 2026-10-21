// The C3 WebSocket client (MOB-014, contracts/ws-protocol.md, architecture
// §10.4): `auth` on connect (the token never goes in the URL, SEC-011), `hello`
// (server clock offset), then `resume` from the last event ID or, without
// one, a REST refetch; `pong` for every `ping`; a refetch on
// `resync_required`; reconnect with backoff 1, 2, 4, 8, 16, 30 s ±20 %; a new
// `auth` on the open socket when the token is refreshed.
//
// The connection status feeds the stale-state gate (MOB-015): Approve needs
// `connectedSynced`, which holds only after `hello` and once any resume or
// refetch has caught up, while messages (pings included) keep arriving.

import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/ws_event.dart';

enum ConnectionStatus { connectedSynced, connecting, stale, offline }

/// One open socket, as the client needs it; tests use a fake.
abstract interface class WsConnection {
  /// Text frames; done when the socket closes.
  Stream<String> get messages;
  void send(String text);
  Future<void> close([int? code]);
  int? get closeCode;
}

typedef WsConnector = Future<WsConnection> Function(Uri url);

/// The signed-in session, as the socket needs it.
abstract interface class TokenSource {
  /// The current access token, or null when signed out.
  Future<String?> accessToken();

  /// Refreshes the session after a 4401; false when that fails.
  Future<bool> refresh();

  /// Every new access token after a refresh.
  Stream<String> get refreshed;
}

const connectTimeout = Duration(seconds: 10);

/// Opens a real socket (web_socket_channel), failing after [connectTimeout].
Future<WsConnection> connectWebSocket(Uri url) async {
  final channel = WebSocketChannel.connect(url);
  await channel.ready.timeout(connectTimeout);
  return _ChannelConnection(channel);
}

class _ChannelConnection implements WsConnection {
  _ChannelConnection(this._channel);

  final WebSocketChannel _channel;

  @override
  Stream<String> get messages =>
      _channel.stream.where((m) => m is String).cast<String>();

  @override
  void send(String text) => _channel.sink.add(text);

  @override
  Future<void> close([int? code]) async => _channel.sink.close(code);

  @override
  int? get closeCode => _channel.closeCode;
}

/// `<ms>-<seq>` stream IDs in order (C3 §5).
int compareEventIds(String a, String b) {
  List<int> parts(String id) => id.split('-').map(int.parse).toList();
  final (x, y) = (parts(a), parts(b));
  return x[0] != y[0] ? x[0].compareTo(y[0]) : x[1].compareTo(y[1]);
}

class WsClient {
  WsClient({
    required this.url,
    required this._connect,
    required this._tokens,
    required this._refetch,
    this._onHello,
    this._onSignedOut,
    DateTime Function()? clock,
    Random? random,
  }) : _clock = clock ?? DateTime.now,
       _random = random ?? Random();

  static const backoff = [1, 2, 4, 8, 16, 30];
  static const staleAfter = Duration(seconds: 30);
  static const offlineAfter = Duration(seconds: 3);

  /// No message at all for this long: the socket is dead; reconnect.
  static const deadAfter = Duration(seconds: 45);
  static const unauthorized = 4401;

  final Uri url;
  final WsConnector _connect;
  final TokenSource _tokens;
  final Future<void> Function() _refetch;
  final void Function(DateTime serverTime)? _onHello;
  final void Function()? _onSignedOut;
  final DateTime Function() _clock;
  final Random _random;

  final _events = StreamController<WsEvent>.broadcast();
  final _statuses = StreamController<ConnectionStatus>.broadcast();

  /// The last event received; sent in `resume` after a reconnect.
  String? lastEventId;

  ConnectionStatus _status = ConnectionStatus.offline;
  WsConnection? _socket;
  StreamSubscription<String>? _messages;
  StreamSubscription<String>? _refreshes;
  Timer? _retry;
  Timer? _tick;
  int _attempt = 0;
  bool _running = false;
  bool _synced = false;
  String? _catchUpTo;
  DateTime? _lastMessageAt;
  DateTime? _closedAt;

  Stream<WsEvent> get events => _events.stream;
  Stream<ConnectionStatus> get statuses => _statuses.stream;
  ConnectionStatus get status => _status;

  void start() {
    if (_running) return;
    _running = true;
    _closedAt = _clock();
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => _check());
    unawaited(_refreshes?.cancel()); // from a run that ended by signing out
    _refreshes = _tokens.refreshed.listen(_reauth);
    unawaited(_open());
  }

  Future<void> stop() async {
    _running = false;
    _retry?.cancel();
    _tick?.cancel();
    _drop(close: true);
    _setStatus(ConnectionStatus.offline);
    await _refreshes?.cancel();
  }

  Future<void> dispose() async {
    await stop();
    await _events.close();
    await _statuses.close();
  }

  // --- connection ---------------------------------------------------------------

  Future<void> _open() async {
    _retry = null;
    if (!_running) return;
    _update();
    final token = await _tokens.accessToken();
    if (token == null) return _signedOut();
    final WsConnection socket;
    try {
      socket = await _connect(url);
    } on Exception {
      return _scheduleRetry();
    }
    if (!_running) {
      await socket.close();
      return;
    }
    _socket = socket;
    _synced = false;
    _catchUpTo = null;
    _lastMessageAt = _clock();
    _messages = socket.messages.listen(
      _onMessage,
      onDone: () => unawaited(_onClosed(socket)),
      onError: (Object _) {}, // onDone follows
    );
    socket.send(jsonEncode({'type': 'auth', 'token': token}));
    _update();
  }

  Future<void> _onClosed(WsConnection socket) async {
    if (!identical(socket, _socket)) return;
    final code = socket.closeCode;
    _drop();
    if (!_running) return;
    if (code == unauthorized && !await _tokens.refresh()) return _signedOut();
    _scheduleRetry();
  }

  /// Forgets the socket at once (the status changes now, not when the close
  /// completes); a socket that may still be open is closed in the background.
  void _drop({bool close = false}) {
    final socket = _socket;
    final messages = _messages;
    _socket = null;
    _messages = null;
    _synced = false;
    _catchUpTo = null;
    _closedAt = _clock();
    _update();
    unawaited(messages?.cancel());
    if (close) unawaited(socket?.close());
  }

  void _scheduleRetry() {
    if (!_running || _retry != null) return;
    final base = backoff[min(_attempt, backoff.length - 1)];
    _attempt++;
    final jitter = 0.8 + 0.4 * _random.nextDouble();
    _retry = Timer(
      Duration(milliseconds: (base * 1000 * jitter).round()),
      () => unawaited(_open()),
    );
  }

  void _signedOut() {
    _running = false;
    _retry?.cancel();
    _tick?.cancel();
    _setStatus(ConnectionStatus.offline);
    _onSignedOut?.call();
  }

  void _reauth(String token) {
    _socket?.send(jsonEncode({'type': 'auth', 'token': token}));
  }

  // --- messages -------------------------------------------------------------------

  void _onMessage(String text) {
    _lastMessageAt = _clock();
    final Object? decoded;
    try {
      decoded = jsonDecode(text);
    } on FormatException {
      return _update();
    }
    if (decoded is! Map<String, dynamic>) return _update();
    switch (decoded['type']) {
      case 'hello':
        _onHelloMessage(decoded);
      case 'ping':
        _socket?.send(jsonEncode({'type': 'pong'}));
      case 'resync_required':
        _catchUpTo = null;
        unawaited(_resync());
      case 'event':
        _onEvent(decoded);
      default:
        break; // an unknown type is ignored (MOB-020)
    }
    _update();
  }

  void _onHelloMessage(Map<String, dynamic> hello) {
    _attempt = 0;
    final serverTime = DateTime.tryParse('${hello['server_time']}');
    if (serverTime != null) _onHello?.call(serverTime);
    final latest = hello['latest_event_id'] as String?;
    final last = lastEventId;
    if (last == null) {
      // First connect: the snapshot covers everything up to `latest`.
      lastEventId = latest;
      unawaited(_resync());
      return;
    }
    _socket?.send(jsonEncode({'type': 'resume', 'last_event_id': last}));
    if (latest == null || compareEventIds(last, latest) >= 0) {
      _synced = true;
    } else {
      _catchUpTo = latest;
    }
  }

  void _onEvent(Map<String, dynamic> json) {
    final WsEvent event;
    try {
      event = WsEvent.fromJson(json);
    } on TypeError {
      return; // a malformed event is dropped; the next refetch repairs state
    } on FormatException {
      return;
    }
    final last = lastEventId;
    if (last == null || compareEventIds(event.eventId, last) > 0) {
      lastEventId = event.eventId;
    }
    _events.add(event);
    final target = _catchUpTo;
    if (target != null && compareEventIds(event.eventId, target) >= 0) {
      _catchUpTo = null;
      _synced = true;
    }
  }

  Future<void> _resync() async {
    final socket = _socket;
    _synced = false;
    _update();
    try {
      await _refetch();
    } on Exception {
      // Not synced: reconnecting starts over with a refetch or a resume.
      if (identical(socket, _socket)) _drop(close: true);
      return _scheduleRetry();
    }
    if (identical(socket, _socket) && _catchUpTo == null) {
      _synced = true;
      _update();
    }
  }

  // --- status -----------------------------------------------------------------------

  void _check() {
    final last = _lastMessageAt;
    if (_socket != null &&
        last != null &&
        _clock().difference(last) >= deadAfter) {
      _drop(close: true);
      return _scheduleRetry();
    }
    _update();
  }

  void _update() {
    final now = _clock();
    final ConnectionStatus next;
    if (_socket == null) {
      final closed = _closedAt;
      next = closed != null && now.difference(closed) >= offlineAfter
          ? ConnectionStatus.offline
          : ConnectionStatus.connecting;
    } else if (now.difference(_lastMessageAt ?? now) >= staleAfter) {
      next = ConnectionStatus.stale;
    } else {
      next = _synced
          ? ConnectionStatus.connectedSynced
          : ConnectionStatus.connecting;
    }
    _setStatus(next);
  }

  void _setStatus(ConnectionStatus next) {
    if (next == _status) return;
    _status = next;
    if (!_statuses.isClosed) _statuses.add(next);
  }
}

/// The live socket, set up in `main.dart` with the live repository; null in mock
/// mode, where there is no socket to lose.
final wsClientProvider = Provider<WsClient?>((ref) => null);

/// connectionStatusProvider of architecture §10.3: the socket's status, or
/// `connectedSynced` when there is no socket (mock mode).
class ConnectionStatusNotifier extends Notifier<ConnectionStatus> {
  @override
  ConnectionStatus build() {
    final client = ref.watch(wsClientProvider);
    if (client == null) return ConnectionStatus.connectedSynced;
    final subscription = client.statuses.listen((status) => state = status);
    ref.onDispose(() => unawaited(subscription.cancel()));
    return client.status;
  }
}

final connectionStatusProvider =
    NotifierProvider<ConnectionStatusNotifier, ConnectionStatus>(
      ConnectionStatusNotifier.new,
    );
