// MOB-001: the session lives in secure storage, survives a restart, and
// sign-out clears it.

import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/auth/auth_repository.dart';
import 'package:oncallpilot/data/auth/secure_session_storage.dart';

import '../../support/fakes.dart';

void main() {
  test('Supabase session storage writes only to the secure store', () async {
    final store = InMemoryStore();
    final storage = SecureSessionStorage(store);
    await storage.initialize();
    expect(await storage.hasAccessToken(), isFalse);

    await storage.persistSession('{"access_token":"a","refresh_token":"r"}');
    expect(store.values.keys, [sessionKey]);
    expect(await storage.hasAccessToken(), isTrue);
    expect(await storage.accessToken(), contains('refresh_token'));

    // A new instance over the same store is the app after a restart.
    expect(await SecureSessionStorage(store).hasAccessToken(), isTrue);

    await storage.removePersistedSession();
    expect(store.values, isEmpty);
  });

  test(
    'PKCE storage is in the secure store too, under its own prefix',
    () async {
      final store = InMemoryStore();
      final storage = SecureAsyncStorage(store);
      await storage.setItem(key: 'verifier', value: 'v');
      expect(await storage.getItem(key: 'verifier'), 'v');
      expect(store.values.keys.single, isNot(sessionKey));
      await storage.removeItem(key: 'verifier');
      expect(store.values, isEmpty);
    },
  );

  test('mock sign-in survives a restart and sign-out clears it', () async {
    final store = InMemoryStore();
    final first = await MockAuthRepository.load(store);
    expect(first.currentUser, isNull);
    await expectLater(
      first.signIn(email: 'oncall@example.com', password: '123'),
      throwsA(isA<AuthFailure>()),
    );
    final changes = <AuthUser?>[];
    first.authStateChanges().listen(changes.add);
    await first.signIn(email: 'oncall@example.com', password: 'secret-pw');
    expect(first.currentUser!.email, 'oncall@example.com');

    final restarted = await MockAuthRepository.load(store);
    expect(restarted.currentUser!.email, 'oncall@example.com');

    await first.signOut();
    expect(store.values, isEmpty);
    expect((await MockAuthRepository.load(store)).currentUser, isNull);
    await settle();
    expect(changes.map((u) => u?.email), ['oncall@example.com', null]);
  });
}
