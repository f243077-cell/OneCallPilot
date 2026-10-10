// Build-time configuration from --dart-define (architecture §10.1, CLAUDE.md §4.6).
//
//   flutter run --dart-define=DATA_SOURCE=mock
//   flutter run --dart-define=DATA_SOURCE=live \
//     --dart-define=API_BASE_URL=http://192.168.1.20:8000 \
//     --dart-define=WS_URL=ws://192.168.1.20:8000/ws \
//     --dart-define=SUPABASE_URL=https://<project>.supabase.co \
//     --dart-define=SUPABASE_ANON_KEY=<public anon key>
//
// The Supabase anon key is public by design; no other secret ever goes here.

import 'package:flutter_riverpod/flutter_riverpod.dart';

enum DataSource { mock, live }

class Env {
  const Env({
    required this.dataSource,
    this.apiBaseUrl = '',
    this.wsUrl = '',
    this.supabaseUrl = '',
    this.supabaseAnonKey = '',
  });

  /// The values compiled into this build.
  factory Env.fromDefines() => Env(
    dataSource: parseDataSource(
      const String.fromEnvironment('DATA_SOURCE', defaultValue: 'mock'),
    ),
    apiBaseUrl: const String.fromEnvironment('API_BASE_URL'),
    wsUrl: const String.fromEnvironment('WS_URL'),
    supabaseUrl: const String.fromEnvironment('SUPABASE_URL'),
    supabaseAnonKey: const String.fromEnvironment('SUPABASE_ANON_KEY'),
  );

  final DataSource dataSource;
  final String apiBaseUrl;
  final String wsUrl;
  final String supabaseUrl;
  final String supabaseAnonKey;

  bool get isMock => dataSource == DataSource.mock;

  /// Names of the defines a live build lacks; empty in mock mode.
  List<String> get missing {
    if (isMock) return const [];
    return [
      if (apiBaseUrl.isEmpty) 'API_BASE_URL',
      if (wsUrl.isEmpty) 'WS_URL',
      if (supabaseUrl.isEmpty) 'SUPABASE_URL',
      if (supabaseAnonKey.isEmpty) 'SUPABASE_ANON_KEY',
    ];
  }

  static DataSource parseDataSource(String value) => switch (value) {
    'mock' => DataSource.mock,
    'live' => DataSource.live,
    _ => throw ArgumentError.value(
      value,
      'DATA_SOURCE',
      'must be "mock" or "live"',
    ),
  };
}

/// The build's configuration; tests override it.
final envProvider = Provider<Env>((ref) => Env.fromDefines());
