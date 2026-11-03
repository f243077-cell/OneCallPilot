// Push notifications (architecture §10.5, MOB-016, task A3.2): ask for the
// notification permission (Android 13+), register the FCM token on sign-in and
// on every token refresh (`POST /devices`), delete the device row before
// sign-out, and turn a notification tap into an incident ID for the router.
// A push carries only the five C9 keys; the app fetches the rest over REST.

import 'dart:async';

import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/errors/api_error.dart';
import '../api/rest_client.dart';
import '../auth/auth_repository.dart';
import '../auth/key_value_store.dart';

/// The parts of FCM the app uses, so the service can be tested without it.
abstract interface class PushMessaging {
  /// True when notifications may be shown.
  Future<bool> requestPermission();
  Future<String?> token();
  Stream<String> get tokenRefreshes;

  /// The data of the notification that started the app, if a tap did.
  Future<Map<String, dynamic>?> initialTap();

  /// The data of each notification tapped while the app runs in the background.
  Stream<Map<String, dynamic>> get taps;
}

class FirebasePushMessaging implements PushMessaging {
  FirebasePushMessaging(this._fcm);

  final FirebaseMessaging _fcm;

  @override
  Future<bool> requestPermission() async {
    final settings = await _fcm.requestPermission();
    return settings.authorizationStatus == AuthorizationStatus.authorized ||
        settings.authorizationStatus == AuthorizationStatus.provisional;
  }

  // getToken and onTokenRefresh are deprecated in favour of register() and
  // onRegistered, which yield a Firebase Installation ID for direct send. The
  // backend sends to FCM registration tokens (C2 `fcm_token`, C9), so these stay
  // until the contract changes.
  @override
  // ignore: deprecated_member_use
  Future<String?> token() => _fcm.getToken();

  @override
  // ignore: deprecated_member_use
  Stream<String> get tokenRefreshes => _fcm.onTokenRefresh;

  @override
  Future<Map<String, dynamic>?> initialTap() async =>
      (await _fcm.getInitialMessage())?.data;

  @override
  Stream<Map<String, dynamic>> get taps =>
      FirebaseMessaging.onMessageOpenedApp.map((message) => message.data);
}

/// `POST /devices` and `DELETE /devices/{id}` (API-011).
abstract interface class DeviceApi {
  /// Returns the device ID.
  Future<String> register({
    required String fcmToken,
    required String appVersion,
  });
  Future<void> remove(String deviceId);
}

class RestDeviceApi implements DeviceApi {
  RestDeviceApi(this._rest);

  final RestClient _rest;

  @override
  Future<String> register({
    required String fcmToken,
    required String appVersion,
  }) async {
    final body = await _rest.post('/devices', {
      'fcm_token': fcmToken,
      'platform': 'android',
      'app_version': appVersion,
    });
    return body['device_id'] as String;
  }

  @override
  Future<void> remove(String deviceId) => _rest.delete('/devices/$deviceId');
}

class PushService {
  PushService(this._messaging, this._devices, this._store, this._appVersion);

  /// The device ID sits in secure storage, like the session (§10.5).
  static const deviceIdKey = 'ocp.push.device_id';
  static final _uuid = RegExp(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
  );

  final PushMessaging _messaging;
  final DeviceApi _devices;
  final KeyValueStore _store;
  final String _appVersion;
  StreamSubscription<String>? _refreshes;

  /// After sign-in: permission, then the token now and after every refresh.
  Future<void> signedIn() async {
    if (_refreshes != null) return;
    _refreshes = _messaging.tokenRefreshes.listen(
      (token) => unawaited(_register(token)),
    );
    await _messaging.requestPermission();
    final token = await _messaging.token();
    if (token != null) await _register(token);
  }

  /// Before sign-out, while the session can still call the API. Best effort:
  /// a row left behind is deleted by the backend once FCM reports the token
  /// unregistered (FR-018).
  Future<void> signingOut() async {
    await _refreshes?.cancel();
    _refreshes = null;
    final deviceId = await _store.read(deviceIdKey);
    if (deviceId == null) return;
    try {
      await _devices.remove(deviceId);
    } on ApiError {
      // Already gone (404) or refused; the row is the backend's to clean up.
    } on NetworkError {
      // No connection; as above.
    }
    await _store.delete(deviceIdKey);
  }

  Future<void> _register(String token) async {
    try {
      final deviceId = await _devices.register(
        fcmToken: token,
        appVersion: _appVersion,
      );
      await _store.write(deviceIdKey, deviceId);
    } on ApiError {
      // Tried again at the next sign-in or token refresh.
    } on NetworkError {
      // As above.
    }
  }

  /// Notification taps: the one that started the app, then each later one.
  /// Data without a valid incident ID is ignored.
  ///
  /// Not an `async*` generator: cancelling one that waits in `await for` on a
  /// broadcast stream never completes.
  Stream<PushTap> incidentTaps() {
    final out = StreamController<PushTap>();
    StreamSubscription<Map<String, dynamic>>? later;
    void emit(Map<String, dynamic>? data) {
      final id = _incidentId(data);
      if (id != null && !out.isClosed) out.add(PushTap(id));
    }

    out
      ..onListen = () async {
        final first = await _messaging.initialTap();
        if (out.isClosed) return;
        emit(first);
        later = _messaging.taps.listen(emit);
      }
      ..onCancel = () async {
        await later?.cancel();
        await out.close();
      };
    return out.stream;
  }

  static String? _incidentId(Map<String, dynamic>? data) {
    final id = data?['incident_id'];
    return id is String && _uuid.hasMatch(id) ? id : null;
  }
}

/// One tap. Two taps on the same incident are two events (no value equality),
/// so the second still opens it.
class PushTap {
  PushTap(this.incidentId);

  final String incidentId;
}

/// Deletes the device row before the session ends (architecture §10.5).
class PushAuthRepository implements AuthRepository {
  PushAuthRepository(this._inner, this._push);

  final AuthRepository _inner;
  final PushService _push;

  @override
  AuthUser? get currentUser => _inner.currentUser;

  @override
  Stream<AuthUser?> authStateChanges() => _inner.authStateChanges();

  @override
  Future<void> signIn({required String email, required String password}) =>
      _inner.signIn(email: email, password: password);

  @override
  Future<void> signOut() async {
    await _push.signingOut();
    await _inner.signOut();
  }
}

/// Set in `main.dart` for a live build with Firebase; null in mock mode.
final pushServiceProvider = Provider<PushService?>((ref) => null);

final pushTapsProvider = StreamProvider<PushTap>((ref) {
  final push = ref.watch(pushServiceProvider);
  return push == null ? const Stream.empty() : push.incidentTaps();
});
