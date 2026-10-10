// Supabase session storage in secure storage instead of shared preferences
// (MOB-001, architecture §10.5): the refresh token sits in Keystore-backed
// encrypted storage, and nothing auth-related goes to shared preferences.

import 'package:supabase_flutter/supabase_flutter.dart';

import 'key_value_store.dart';

const sessionKey = 'ocp.auth.session';
const _asyncPrefix = 'ocp.auth.async.';

/// Replaces supabase_flutter's SharedPreferencesLocalStorage.
class SecureSessionStorage extends LocalStorage {
  SecureSessionStorage(this._store);

  final KeyValueStore _store;

  @override
  Future<void> initialize() async {}

  @override
  Future<bool> hasAccessToken() async => await _store.read(sessionKey) != null;

  @override
  Future<String?> accessToken() => _store.read(sessionKey);

  @override
  Future<void> persistSession(String persistSessionString) =>
      _store.write(sessionKey, persistSessionString);

  @override
  Future<void> removePersistedSession() => _store.delete(sessionKey);
}

/// Replaces SharedPreferencesGotrueAsyncStorage (used by PKCE flows).
class SecureAsyncStorage extends GotrueAsyncStorage {
  SecureAsyncStorage(this._store);

  final KeyValueStore _store;

  @override
  Future<String?> getItem({required String key}) =>
      _store.read('$_asyncPrefix$key');

  @override
  Future<void> setItem({required String key, required String value}) =>
      _store.write('$_asyncPrefix$key', value);

  @override
  Future<void> removeItem({required String key}) =>
      _store.delete('$_asyncPrefix$key');
}
