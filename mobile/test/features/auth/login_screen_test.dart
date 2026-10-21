// TEST-011, MOB-001: the login screen's own states — field validation before
// any call, and a busy button while the sign-in is in flight.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/config/env.dart';
import 'package:oncallpilot/data/auth/auth_repository.dart';
import 'package:oncallpilot/features/auth/application/auth_session.dart';
import 'package:oncallpilot/features/auth/presentation/login_screen.dart';

class _SlowAuth implements AuthRepository {
  final pending = Completer<void>();
  int calls = 0;

  @override
  AuthUser? get currentUser => null;

  @override
  Stream<AuthUser?> authStateChanges() => const Stream.empty();

  @override
  Future<void> signIn({required String email, required String password}) {
    calls++;
    return pending.future;
  }

  @override
  Future<void> signOut() async {}
}

void main() {
  late _SlowAuth auth;

  Future<void> pump(WidgetTester tester) async {
    auth = _SlowAuth();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          authRepositoryProvider.overrideWithValue(auth),
          envProvider.overrideWithValue(const Env(dataSource: DataSource.mock)),
        ],
        child: const MaterialApp(home: LoginScreen()),
      ),
    );
  }

  testWidgets('empty fields are refused before any call', (tester) async {
    await pump(tester);
    await tester.tap(find.text('Sign in'));
    await tester.pump();
    expect(find.text('Enter your email'), findsOneWidget);
    expect(find.text('Enter your password'), findsOneWidget);
    expect(auth.calls, 0);
  });

  testWidgets('the button is busy while signing in', (tester) async {
    await pump(tester);
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Email'),
      'a@b.c',
    );
    await tester.enterText(
      find.widgetWithText(TextFormField, 'Password'),
      'secret-pw',
    );
    await tester.tap(find.text('Sign in'));
    await tester.pump();
    expect(auth.calls, 1);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    final button = tester.widget<ButtonStyleButton>(
      find.ancestor(
        of: find.byType(CircularProgressIndicator),
        matching: find.byWidgetPredicate((w) => w is ButtonStyleButton),
      ),
    );
    expect(button.onPressed, isNull, reason: 'no second tap');
    auth.pending.complete();
    await tester.pumpAndSettle();
    expect(find.text('Sign in'), findsOneWidget);
  });
}
