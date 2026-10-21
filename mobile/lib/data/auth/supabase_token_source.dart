// The Supabase session as the REST client and the socket need it (C3 §3 step
// 5): the current access token, a refresh after a 401 or 4401, and every
// token the client refreshes on its own.

import 'package:supabase_flutter/supabase_flutter.dart' as supa;

import '../api/ws_client.dart';

class SupabaseTokenSource implements TokenSource {
  SupabaseTokenSource(this._auth);

  final supa.GoTrueClient _auth;

  @override
  Future<String?> accessToken() async => _auth.currentSession?.accessToken;

  @override
  Future<bool> refresh() async {
    if (_auth.currentSession == null) return false;
    try {
      final response = await _auth.refreshSession();
      return response.session != null;
    } on supa.AuthException {
      return false;
    }
  }

  @override
  Stream<String> get refreshed => _auth.onAuthStateChange
      .where((state) => state.event == supa.AuthChangeEvent.tokenRefreshed)
      .map((state) => state.session?.accessToken)
      .where((token) => token != null)
      .cast<String>();
}
