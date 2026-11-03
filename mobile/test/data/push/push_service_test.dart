// A3.2 / MOB-016: the token is registered on sign-in and on every refresh,
// the device row is deleted before sign-out, failures never break sign-in or
// sign-out, and a notification tap opens its incident through the router.

import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/errors/api_error.dart';
import 'package:oncallpilot/data/api/rest_client.dart';
import 'package:oncallpilot/data/auth/auth_repository.dart';
import 'package:oncallpilot/data/push/push_service.dart';
import 'package:oncallpilot/features/incident_detail/presentation/incident_detail_screen.dart';

import '../../support/fakes.dart';
import '../../support/sample_timeline.dart' show incidentId;
import '../../support/test_app.dart';
import '../repositories/live_incident_repository_test.dart'
    show FakeAdapter, FakeTokens;

class FakeMessaging implements PushMessaging {
  String? currentToken = 'token-1';
  Map<String, dynamic>? startTap;
  final refreshes = StreamController<String>.broadcast();
  final tapped = StreamController<Map<String, dynamic>>.broadcast();
  int permissionRequests = 0;

  @override
  Future<bool> requestPermission() async {
    permissionRequests++;
    return true;
  }

  @override
  Future<String?> token() async => currentToken;

  @override
  Stream<String> get tokenRefreshes => refreshes.stream;

  @override
  Future<Map<String, dynamic>?> initialTap() async => startTap;

  @override
  Stream<Map<String, dynamic>> get taps => tapped.stream;
}

class FakeDevices implements DeviceApi {
  final registered = <(String token, String version)>[];
  final removed = <String>[];
  Exception? failRegister;
  Exception? failRemove;

  @override
  Future<String> register({
    required String fcmToken,
    required String appVersion,
  }) async {
    if (failRegister != null) throw failRegister!;
    registered.add((fcmToken, appVersion));
    return 'device-${registered.length}';
  }

  @override
  Future<void> remove(String deviceId) async {
    if (failRemove != null) throw failRemove!;
    removed.add(deviceId);
  }
}

class RecordingAuth implements AuthRepository {
  RecordingAuth(this.calls);

  final List<String> calls;

  @override
  AuthUser? get currentUser => null;

  @override
  Stream<AuthUser?> authStateChanges() => const Stream.empty();

  @override
  Future<void> signIn({required String email, required String password}) async {}

  @override
  Future<void> signOut() async => calls.add('auth signOut');
}

void main() {
  late FakeMessaging messaging;
  late FakeDevices devices;
  late InMemoryStore store;
  late PushService push;

  setUp(() {
    messaging = FakeMessaging();
    devices = FakeDevices();
    store = InMemoryStore();
    push = PushService(messaging, devices, store, '1.0.0+1');
  });

  test('sign-in asks for permission and registers the token', () async {
    await push.signedIn();
    expect(messaging.permissionRequests, 1);
    expect(devices.registered, [('token-1', '1.0.0+1')]);
    expect(store.values[PushService.deviceIdKey], 'device-1');
  });

  test('a refreshed token is registered again', () async {
    await push.signedIn();
    messaging.refreshes.add('token-2');
    await settle();
    expect(devices.registered.last, ('token-2', '1.0.0+1'));
    expect(store.values[PushService.deviceIdKey], 'device-2');
  });

  test('a second sign-in event does not subscribe twice', () async {
    await push.signedIn();
    await push.signedIn();
    messaging.refreshes.add('token-2');
    await settle();
    expect(devices.registered, hasLength(2));
  });

  test('a failed registration does not break sign-in', () async {
    devices.failRegister = const NetworkError('No connection');
    await push.signedIn();
    expect(store.values, isEmpty);
  });

  test('sign-out deletes the device row and stops following the token', () async {
    await push.signedIn();
    await push.signingOut();
    expect(devices.removed, ['device-1']);
    expect(store.values, isEmpty);
    messaging.refreshes.add('token-2');
    await settle();
    expect(devices.registered, hasLength(1));
  });

  test('sign-out goes on when the row cannot be deleted', () async {
    await push.signedIn();
    devices.failRemove = const ApiError(
      ApiErrorCode.notFound,
      status: 404,
      message: 'gone',
    );
    await push.signingOut();
    expect(store.values, isEmpty);
  });

  test('the device row is deleted before the session ends', () async {
    final calls = <String>[];
    devices = FakeDevicesLogging(calls);
    push = PushService(messaging, devices, store, '1.0.0+1');
    await push.signedIn();
    await PushAuthRepository(RecordingAuth(calls), push).signOut();
    expect(calls, ['remove device-1', 'auth signOut']);
  });

  test('taps yield valid incident IDs only, the starting one first', () async {
    messaging.startTap = {'type': 'incident.opened', 'incident_id': incidentId};
    final ids = <String>[];
    final subscription = push.incidentTaps().listen(
      (tap) => ids.add(tap.incidentId),
    );
    await settle();
    messaging.tapped
      ..add({'incident_id': 'not-a-uuid'})
      ..add({'type': 'incident.resolved'})
      ..add({'incident_id': incidentId});
    await settle();
    await subscription.cancel();
    expect(ids, [incidentId, incidentId]);
  });

  group('RestDeviceApi', () {
    late FakeAdapter adapter;
    late RestDeviceApi api;

    setUp(() {
      adapter = FakeAdapter();
      final dio = Dio()..httpClientAdapter = adapter;
      api = RestDeviceApi(
        RestClient('http://192.168.1.20:8000', FakeTokens(), dio: dio),
      );
    });

    test('registers with the C2 body and the token in the header', () async {
      adapter.replies.add(
        (_) => (200, {'device_id': '66666666-6666-4666-8666-666666666666'}),
      );
      final id = await api.register(fcmToken: 'fcm-abc', appVersion: '1.0.0+1');
      expect(id, '66666666-6666-4666-8666-666666666666');
      final sent = adapter.sent.single;
      expect(sent.options.method, 'POST');
      expect(sent.path, '/devices');
      expect(sent.header('Authorization'), 'Bearer access-1');
      expect(sent.body, {
        'fcm_token': 'fcm-abc',
        'platform': 'android',
        'app_version': '1.0.0+1',
      });
    });

    test('deletes with a 204 and no body', () async {
      adapter.replies.add((_) => (204, null));
      await api.remove('66666666-6666-4666-8666-666666666666');
      final sent = adapter.sent.single;
      expect(sent.options.method, 'DELETE');
      expect(sent.path, '/devices/66666666-6666-4666-8666-666666666666');
    });
  });

  testWidgets('a notification tap opens its incident', (tester) async {
    messaging.startTap = {'type': 'proposal.created', 'incident_id': incidentId};
    final app = await TestApp.create(
      signedIn: true,
      overrides: [pushServiceProvider.overrideWithValue(push)],
    );
    await tester.pumpWidget(app.widget);
    await tester.pumpAndSettle();
    expect(find.byType(IncidentDetailScreen), findsOneWidget);
  });
}

class FakeDevicesLogging extends FakeDevices {
  FakeDevicesLogging(this.calls);

  final List<String> calls;

  @override
  Future<void> remove(String deviceId) async {
    calls.add('remove $deviceId');
    await super.remove(deviceId);
  }
}
