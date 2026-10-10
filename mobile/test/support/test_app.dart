import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:oncallpilot/app.dart';
import 'package:oncallpilot/core/config/env.dart';
import 'package:oncallpilot/data/auth/auth_repository.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';
import 'package:oncallpilot/data/repositories/mock_incident_repository.dart';
import 'package:oncallpilot/features/auth/application/auth_session.dart';

import 'fakes.dart';
import 'sample_timeline.dart';

/// The whole app in mock mode, with in-memory storage and the test timeline.
class TestApp {
  TestApp._(this.container, this.auth);

  static Future<TestApp> create({bool signedIn = false}) async {
    final auth = await MockAuthRepository.load(InMemoryStore());
    if (signedIn) {
      await auth.signIn(email: 'oncall@example.com', password: 'secret-pw');
    }
    final incidents = MockIncidentRepository(
      [sampleTimeline()],
      delay: (_) async {},
      random: Random(1),
    )..start();
    final container = ProviderContainer(
      overrides: [
        envProvider.overrideWithValue(const Env(dataSource: DataSource.mock)),
        authRepositoryProvider.overrideWithValue(auth),
        incidentRepositoryProvider.overrideWithValue(incidents),
      ],
    );
    return TestApp._(container, auth);
  }

  final ProviderContainer container;
  final MockAuthRepository auth;

  Widget get widget => UncontrolledProviderScope(
    container: container,
    child: const OnCallPilotApp(),
  );
}
