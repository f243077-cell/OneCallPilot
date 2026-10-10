// MOB-002: signed-out users go to /login; a deep link such as a push tap's
// /incidents/:id opens after sign-in.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/core/router/app_router.dart';
import 'package:oncallpilot/features/auth/presentation/login_screen.dart';
import 'package:oncallpilot/features/incident_feed/presentation/incident_feed_screen.dart';

import '../../support/sample_timeline.dart';
import '../../support/test_app.dart';

void main() {
  group('authRedirect', () {
    String? go(String location, {required bool signedIn}) =>
        authRedirect(signedIn: signedIn, location: Uri.parse(location));

    test('signed out: everything but /login goes to /login', () {
      expect(go('/incidents', signedIn: false), '/login');
      expect(
        go('/incidents/abc', signedIn: false),
        '/login?from=%2Fincidents%2Fabc',
      );
      expect(go('/login', signedIn: false), isNull);
    });

    test('signed in: /login goes to the saved link or the feed', () {
      expect(go('/login', signedIn: true), '/incidents');
      expect(
        go('/login?from=%2Fproposals%2Fp1', signedIn: true),
        '/proposals/p1',
      );
      expect(go('/incidents/abc', signedIn: true), isNull);
    });

    test('the saved link must be an in-app path', () {
      expect(
        go('/login?from=https%3A%2F%2Fevil.example', signedIn: true),
        '/incidents',
      );
      expect(
        go('/login?from=%2F%2Fevil.example', signedIn: true),
        '/incidents',
      );
    });
  });

  testWidgets('a deep link opens after signing in', (tester) async {
    final app = await TestApp.create();
    await tester.pumpWidget(app.widget);
    await tester.pumpAndSettle();
    expect(find.byType(LoginScreen), findsOneWidget);

    final router = app.container.read(routerProvider);
    router.go('/incidents/$incidentId');
    await tester.pumpAndSettle();
    expect(find.byType(LoginScreen), findsOneWidget);
    expect(
      router.routeInformationProvider.value.uri.toString(),
      '/login?from=${Uri.encodeComponent('/incidents/$incidentId')}',
    );

    await tester.enterText(
      find.byType(TextFormField).at(0),
      'oncall@example.com',
    );
    await tester.enterText(find.byType(TextFormField).at(1), 'secret-pw');
    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();
    expect(
      router.routeInformationProvider.value.uri.path,
      '/incidents/$incidentId',
    );
    expect(find.textContaining(incidentId), findsOneWidget);
  });

  testWidgets('a refused sign-in shows the error and stays on /login', (
    tester,
  ) async {
    final app = await TestApp.create();
    await tester.pumpWidget(app.widget);
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byType(TextFormField).at(0),
      'oncall@example.com',
    );
    await tester.enterText(find.byType(TextFormField).at(1), '123');
    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();
    expect(find.text('Invalid login credentials'), findsOneWidget);
    expect(find.byType(LoginScreen), findsOneWidget);
  });

  testWidgets(
    'signed in: the feed lists mock incidents; sign-out returns to login',
    (tester) async {
      final app = await TestApp.create(signedIn: true);
      await tester.pumpWidget(app.widget);
      await tester.pumpAndSettle();
      expect(find.byType(IncidentFeedScreen), findsOneWidget);
      expect(find.text('api error rate above baseline'), findsOneWidget);

      await tester.tap(find.byTooltip('Settings'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sign out'));
      await tester.pumpAndSettle();
      expect(find.byType(LoginScreen), findsOneWidget);
    },
  );
}
