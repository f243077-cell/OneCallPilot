import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'app.dart';
import 'core/config/env.dart';
import 'data/api/rest_client.dart';
import 'data/api/ws_client.dart';
import 'data/auth/auth_repository.dart';
import 'data/auth/key_value_store.dart';
import 'data/auth/secure_session_storage.dart';
import 'data/auth/supabase_token_source.dart';
import 'data/push/push_service.dart';
import 'data/repositories/fixture_timelines.dart';
import 'data/repositories/incident_repository.dart';
import 'data/repositories/live_incident_repository.dart';
import 'data/repositories/mock_incident_repository.dart';
import 'features/auth/application/auth_session.dart';
import 'features/incident_detail/application/incident_detail_provider.dart';
import 'features/incident_feed/application/incident_list_provider.dart';
import 'shared/server_clock.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final env = Env.fromDefines();
  if (env.missing.isNotEmpty) {
    runApp(
      StartupErrorApp(
        message:
            'This build is missing: ${env.missing.join(', ')}.\n\n'
            'Pass them with --dart-define, or build with DATA_SOURCE=mock.',
      ),
    );
    return;
  }
  final List<Override> overrides;
  try {
    overrides = await _dataLayer(env);
  } on StateError catch (error) {
    runApp(StartupErrorApp(message: error.message));
    return;
  }
  runApp(ProviderScope(overrides: overrides, child: const OnCallPilotApp()));
}

/// Picks the auth and incident repositories for the build's DATA_SOURCE.
Future<List<Override>> _dataLayer(Env env) async {
  final store = SecureKeyValueStore();
  final overrides = <Override>[envProvider.overrideWithValue(env)];
  if (env.isMock) {
    final timelines = await loadTimelineAssets(rootBundle);
    final incidents = MockIncidentRepository(timelines)..start();
    return overrides
      ..add(
        authRepositoryProvider.overrideWithValue(
          await MockAuthRepository.load(store),
        ),
      )
      ..add(incidentRepositoryProvider.overrideWithValue(incidents));
  }
  await Supabase.initialize(
    url: env.supabaseUrl,
    publishableKey: env.supabaseAnonKey,
    authOptions: FlutterAuthClientOptions(
      localStorage: SecureSessionStorage(store),
      pkceAsyncStorage: SecureAsyncStorage(store),
      detectSessionInUri: false,
    ),
  );
  final auth = Supabase.instance.client.auth;
  final tokens = SupabaseTokenSource(auth);
  final rest = RestClient(env.apiBaseUrl, tokens);
  final push = await _push(rest, store);
  final AuthRepository authRepository = SupabaseAuthRepository(auth);
  return overrides..addAll([
    authRepositoryProvider.overrideWithValue(
      push == null ? authRepository : PushAuthRepository(authRepository, push),
    ),
    if (push != null)
      pushServiceProvider.overrideWith((ref) => _followSignIn(ref, push)),
    wsClientProvider.overrideWith((ref) => _liveSocket(ref, env, tokens)),
    incidentRepositoryProvider.overrideWith(
      (ref) => LiveIncidentRepository(rest, ref.watch(wsClientProvider)!),
    ),
  ]);
}

/// Push for a live build (A3.2). Without `android/app/google-services.json`
/// Firebase does not start; the app then works without push.
Future<PushService?> _push(RestClient rest, KeyValueStore store) async {
  try {
    await Firebase.initializeApp();
  } on FirebaseException {
    return null;
  }
  return PushService(
    FirebasePushMessaging(FirebaseMessaging.instance),
    RestDeviceApi(rest),
    store,
    appVersion,
  );
}

/// Registers the device on every sign-in, including a session restored at start.
PushService _followSignIn(Ref ref, PushService push) {
  final session = ref.watch(authStateProvider);
  void follow() {
    if (session.signedIn) unawaited(push.signedIn());
  }

  session.addListener(follow);
  follow();
  ref.onDispose(() => session.removeListener(follow));
  return push;
}

/// The socket of a live build (A3.1): connected while signed in; a first
/// connect or `resync_required` refetches the feed and every open incident;
/// `hello` sets the server clock; a refused session refresh signs out.
WsClient _liveSocket(Ref ref, Env env, TokenSource tokens) {
  final session = ref.watch(authStateProvider);
  final client = WsClient(
    url: Uri.parse(env.wsUrl),
    connect: connectWebSocket,
    tokens: tokens,
    refetch: () async {
      ref
        ..invalidate(incidentListProvider)
        ..invalidate(incidentDetailProvider);
      await ref.read(incidentListProvider.future);
    },
    onHello: (serverTime) =>
        ref.read(serverClockOffsetProvider.notifier).sync(serverTime),
    onSignedOut: () => unawaited(ref.read(authRepositoryProvider).signOut()),
  );
  void follow() => session.signedIn ? client.start() : unawaited(client.stop());
  session.addListener(follow);
  follow();
  ref.onDispose(() {
    session.removeListener(follow);
    unawaited(client.dispose());
  });
  return client;
}
