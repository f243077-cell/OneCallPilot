import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';
import 'package:oncallpilot/features/incident_detail/presentation/incident_detail_screen.dart';
import 'package:oncallpilot/features/incident_feed/presentation/incident_feed_screen.dart';
import 'package:oncallpilot/shared/formatting.dart';

/// The feed and detail screens with their routes, a repository and a clock.
/// Returns the router so a test can read the location.
Future<GoRouter> pumpScreens(
  WidgetTester tester, {
  required IncidentRepository repository,
  required DateTime Function() clock,
  String initialLocation = '/incidents',
  bool settle = true,
}) async {
  final router = GoRouter(
    initialLocation: initialLocation,
    routes: [
      GoRoute(
        path: '/incidents',
        builder: (_, _) => const IncidentFeedScreen(),
      ),
      GoRoute(
        path: '/incidents/:id',
        builder: (_, state) =>
            IncidentDetailScreen(incidentId: state.pathParameters['id']!),
      ),
      GoRoute(
        path: '/proposals/:id',
        builder: (_, state) =>
            Scaffold(body: Text('proposal ${state.pathParameters['id']}')),
      ),
      GoRoute(path: '/settings', builder: (_, _) => const Scaffold()),
    ],
  );
  addTearDown(router.dispose);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        incidentRepositoryProvider.overrideWithValue(repository),
        clockProvider.overrideWithValue(clock),
      ],
      child: MaterialApp.router(routerConfig: router),
    ),
  );
  if (settle) await tester.pumpAndSettle();
  return router;
}
