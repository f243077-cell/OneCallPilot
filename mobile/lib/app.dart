import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'core/router/app_router.dart';
import 'core/theme/app_theme.dart';

/// Root widget (architecture §10.1): `MaterialApp.router` with go_router.
class OnCallPilotApp extends ConsumerWidget {
  const OnCallPilotApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) => MaterialApp.router(
    title: 'OnCallPilot',
    theme: AppTheme.light(),
    darkTheme: AppTheme.dark(),
    routerConfig: ref.watch(routerProvider),
  );
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
