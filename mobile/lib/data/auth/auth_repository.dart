// Sign-in behind one interface (MOB-001): Supabase in live mode, an offline
// stand-in in mock mode. Screens and the router see only this.

import 'dart:async';
import 'dart:convert';

import 'package:supabase_flutter/supabase_flutter.dart' as supa;

import 'key_value_store.dart';

class AuthUser {
  const AuthUser({required this.id, required this.email});

  final String id;
  final String email;
}

/// A sign-in the server refused; [message] is safe to show.
class AuthFailure implements Exception {
  const AuthFailure(this.message);

  final String message;

  @override
  String toString() => 'AuthFailure($message)';
}

abstract interface class AuthRepository {
  AuthUser? get currentUser;

  /// Emits the user after every sign-in and sign-out (null when signed out).
  Stream<AuthUser?> authStateChanges();

  /// Throws [AuthFailure] when the email or password is refused.
  Future<void> signIn({required String email, required String password});

  /// Ends the session and clears it from secure storage. Deleting the device
  /// registration (`DELETE /devices/{id}`) joins this with push, task A3.2.
  Future<void> signOut();
}

/// Email and password through Supabase Auth; the session is kept by
/// SecureSessionStorage, set up in `main.dart`.
class SupabaseAuthRepository implements AuthRepository {
  SupabaseAuthRepository(this._auth);

  final supa.GoTrueClient _auth;

  static AuthUser? _user(supa.User? user) =>
      user == null ? null : AuthUser(id: user.id, email: user.email ?? '');

  @override
  AuthUser? get currentUser => _user(_auth.currentUser);

  @override
  Stream<AuthUser?> authStateChanges() =>
      _auth.onAuthStateChange.map((state) => _user(state.session?.user));

  @override
  Future<void> signIn({required String email, required String password}) async {
    try {
      await _auth.signInWithPassword(email: email, password: password);
    } on supa.AuthException catch (error) {
      throw AuthFailure(error.message);
    }
  }

  @override
  Future<void> signOut() => _auth.signOut();
}

/// Mock mode (MOB-017): no network. Any well-formed email with a password of
/// at least 6 characters signs in; the session survives a restart, kept in
/// secure storage like the real one.
class MockAuthRepository implements AuthRepository {
  MockAuthRepository._(this._store, this._user);

  static const storageKey = 'ocp.mock.session';

  static Future<MockAuthRepository> load(KeyValueStore store) async {
    final saved = await store.read(storageKey);
    final user = saved == null ? null : _decode(saved);
    return MockAuthRepository._(store, user);
  }

  final KeyValueStore _store;
  AuthUser? _user;
  final _changes = StreamController<AuthUser?>.broadcast();

  static AuthUser _decode(String raw) {
    final json = jsonDecode(raw) as Map<String, dynamic>;
    return AuthUser(id: json['id'] as String, email: json['email'] as String);
  }

  @override
  AuthUser? get currentUser => _user;

  @override
  Stream<AuthUser?> authStateChanges() => _changes.stream;

  @override
  Future<void> signIn({required String email, required String password}) async {
    if (!email.contains('@') || password.length < 6) {
      throw const AuthFailure('Invalid login credentials');
    }
    final user = AuthUser(
      id: '00000000-0000-4000-8000-000000000001',
      email: email,
    );
    await _store.write(
      storageKey,
      jsonEncode({'id': user.id, 'email': user.email}),
    );
    _user = user;
    _changes.add(user);
  }

  @override
  Future<void> signOut() async {
    await _store.delete(storageKey);
    _user = null;
    _changes.add(null);
  }
}
