// The live socket as main.dart wires it (A3.1): after `hello` the feed is
// refetched and the connection becomes connectedSynced. The repository watches
// the socket, as in a live build; reading the feed through the socket
// provider's own ref was a circular dependency that kept the app showing
// "Reconnecting — approval is paused" on the first real device.

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/data/api/ws_client.dart';
import 'package:oncallpilot/data/auth/auth_repository.dart';
import 'package:oncallpilot/data/repositories/incident_repository.dart';
import 'package:oncallpilot/features/auth/application/auth_session.dart';
import 'package:oncallpilot/main.dart';

import 'data/api/ws_client_test.dart' show FakeSocket, FakeTokens, hello;
import 'support/fakes.dart';

void main() {
  test(
    'a live socket reaches connectedSynced after hello and the refetch',
    () async {
      final auth = await MockAuthRepository.load(InMemoryStore());
      await auth.signIn(email: 'oncall@example.com', password: 'secret-pw');
      final socket = FakeSocket();
      final repository = FakeIncidentRepository();
      var listed = 0;
      repository.onList = () async {
        listed++;
        return [];
      };
      final container = ProviderContainer(
        overrides: [
          authRepositoryProvider.overrideWithValue(auth),
          wsClientProvider.overrideWith(
            (ref) => liveSocket(
              ref,
              Uri.parse('ws://192.168.1.20:8000/ws/incidents'),
              FakeTokens(),
              connect: (_) async => socket,
            ),
          ),
          incidentRepositoryProvider.overrideWith((ref) {
            ref.watch(wsClientProvider); // as LiveIncidentRepository does
            return repository;
          }),
        ],
      );
      addTearDown(container.dispose);
      final client = container.read(wsClientProvider)!;
      final synced = client.statuses.firstWhere(
        (status) => status == ConnectionStatus.connectedSynced,
      );
      await settle();
      expect(socket.types, ['auth']);
      socket.receive(hello());
      await synced.timeout(const Duration(seconds: 5));
      expect(listed, 1);
    },
  );
}
