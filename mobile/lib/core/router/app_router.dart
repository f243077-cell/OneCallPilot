// Routes of architecture §10.2 with the sign-in redirect (MOB-002). A push tap
// (task A3.2) opens `/incidents/:id` through this router; when signed out,
// the link is kept in `?from=` and opened after login.

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../features/auth/application/auth_session.dart';
import '../../features/auth/presentation/login_screen.dart';
import '../../features/incident_detail/presentation/incident_detail_screen.dart';
import '../../features/incident_feed/presentation/incident_feed_screen.dart';
import '../../features/settings/presentation/settings_screen.dart';
import '../../shared/widgets/placeholder_screen.dart';

const loginPath = '/login';
const homePath = '/incidents';

/// Where to send the user, or null to stay.
String? authRedirect({required bool signedIn, required Uri location}) {
  final atLogin = location.path == loginPath;
  if (!signedIn) {
    if (atLogin) return null;
    final from = location.toString();
    return from == homePath
        ? loginPath
        : '$loginPath?from=${Uri.encodeComponent(from)}';
  }
  if (!atLogin) return null;
  final from = location.queryParameters['from'];
  // Only an in-app path: never a scheme, host or protocol-relative URL.
  final safe = from != null && from.startsWith('/') && !from.startsWith('//');
  return safe ? from : homePath;
}

GoRouter buildRouter(AuthSession session) => GoRouter(
  initialLocation: homePath,
  refreshListenable: session,
  redirect: (context, state) =>
      authRedirect(signedIn: session.signedIn, location: state.uri),
  routes: [
    GoRoute(path: '/', redirect: (_, _) => homePath),
    GoRoute(path: loginPath, builder: (_, _) => const LoginScreen()),
    GoRoute(path: homePath, builder: (_, _) => const IncidentFeedScreen()),
    GoRoute(
      path: '/incidents/:id',
      builder: (_, state) =>
          IncidentDetailScreen(incidentId: state.pathParameters['id']!),
    ),
    GoRoute(
      path: '/proposals/:id',
      builder: (_, state) => PlaceholderScreen(
        title: 'Action review',
        task: 'A2.7',
        id: state.pathParameters['id'],
      ),
    ),
    GoRoute(
      path: '/executions/:id',
      builder: (_, state) => PlaceholderScreen(
        title: 'Execution',
        task: 'A2.9',
        id: state.pathParameters['id'],
      ),
    ),
    GoRoute(
      path: '/audit',
      builder: (_, _) =>
          const PlaceholderScreen(title: 'Audit history', task: 'A3.5'),
    ),
    GoRoute(path: '/settings', builder: (_, _) => const SettingsScreen()),
  ],
);

final routerProvider = Provider<GoRouter>((ref) {
  final router = buildRouter(ref.watch(authStateProvider));
  ref.onDispose(router.dispose);
  return router;
});
