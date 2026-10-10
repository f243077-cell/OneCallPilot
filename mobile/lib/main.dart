import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'app.dart';
import 'core/config/env.dart';
import 'data/auth/auth_repository.dart';
import 'data/auth/key_value_store.dart';
import 'data/auth/secure_session_storage.dart';
import 'data/repositories/fixture_timelines.dart';
import 'data/repositories/incident_repository.dart';
import 'data/repositories/mock_incident_repository.dart';
import 'features/auth/application/auth_session.dart';

// Firebase init joins with push in task A3.2 (architecture §10.1).
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
  return overrides..add(
    authRepositoryProvider.overrideWithValue(
      SupabaseAuthRepository(Supabase.instance.client.auth),
    ),
  );
}
