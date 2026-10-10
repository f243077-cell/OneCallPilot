// authStateProvider of architecture §10.3: the signed-in user, as a Listenable
// so go_router re-runs its redirect on every sign-in and sign-out.

import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../data/auth/auth_repository.dart';

class AuthSession extends ChangeNotifier {
  AuthSession(AuthRepository repository) : _user = repository.currentUser {
    _subscription = repository.authStateChanges().listen((user) {
      _user = user;
      notifyListeners();
    });
  }

  AuthUser? _user;
  late final StreamSubscription<AuthUser?> _subscription;

  AuthUser? get user => _user;
  bool get signedIn => _user != null;

  @override
  void dispose() {
    unawaited(_subscription.cancel());
    super.dispose();
  }
}

/// Set in `main.dart`: Supabase in live mode, MockAuthRepository in mock mode.
final authRepositoryProvider = Provider<AuthRepository>(
  (ref) => throw UnimplementedError('authRepositoryProvider is set in main'),
);

final authStateProvider = Provider<AuthSession>((ref) {
  final session = AuthSession(ref.watch(authRepositoryProvider));
  ref.onDispose(session.dispose);
  return session;
});
