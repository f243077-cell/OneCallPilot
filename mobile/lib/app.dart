import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/router/app_router.dart';
import 'core/theme/app_theme.dart';
import 'data/push/push_service.dart';

/// Root widget (architecture §10.1): `MaterialApp.router` with go_router.
class OnCallPilotApp extends ConsumerWidget {
  const OnCallPilotApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);
    // A notification tap opens its incident (MOB-016); when signed out, the
    // router keeps the link and opens it after login.
    ref.listen(pushTapsProvider, (_, next) {
      final tap = next.value;
      if (tap != null) unawaited(router.push('/incidents/${tap.incidentId}'));
    });
    return MaterialApp.router(
      title: 'OnCallPilot',
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      routerConfig: router,
    );
  }
}

/// Shown instead of the app when it cannot start: a live build without its
/// --dart-define values, or a mock build without synced fixtures.
class StartupErrorApp extends StatelessWidget {
  const StartupErrorApp({super.key, required this.message});

  final String message;

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'OnCallPilot',
    theme: AppTheme.light(),
    home: Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(message, textAlign: TextAlign.center),
        ),
      ),
    ),
  );
}
