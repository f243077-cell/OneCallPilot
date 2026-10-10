import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/config/env.dart';

void main() {
  test('DATA_SOURCE is mock or live', () {
    expect(Env.parseDataSource('mock'), DataSource.mock);
    expect(Env.parseDataSource('live'), DataSource.live);
    expect(() => Env.parseDataSource('fake'), throwsArgumentError);
  });

  test('a test build defaults to mock mode and needs nothing else', () {
    final env = Env.fromDefines();
    expect(env.isMock, isTrue);
    expect(env.missing, isEmpty);
  });

  test('a live build names every missing define', () {
    expect(const Env(dataSource: DataSource.live).missing, [
      'API_BASE_URL',
      'WS_URL',
      'SUPABASE_URL',
      'SUPABASE_ANON_KEY',
    ]);
    const complete = Env(
      dataSource: DataSource.live,
      apiBaseUrl: 'http://192.168.1.20:8000',
      wsUrl: 'ws://192.168.1.20:8000/ws',
      supabaseUrl: 'https://x.supabase.co',
      supabaseAnonKey: 'public-anon-key',
    );
    expect(complete.missing, isEmpty);
  });
}
