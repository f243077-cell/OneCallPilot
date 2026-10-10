# OnCallPilot app (Android)

Flutter app of OnCallPilot (architecture §10). Riverpod for state, go_router for
routes, Supabase Auth for sign-in with the session in secure storage.

## Mock mode (no backend, no network)

`MockIncidentRepository` replays the contract fixture timelines
(`contracts/fixtures/timelines/`). Copy them into the git-ignored
`assets/fixtures/timelines/` first:

```sh
dart run tool/sync_fixtures.dart                       # from ../contracts
dart run tool/sync_fixtures.dart --from <contracts>    # from another checkout
flutter run --dart-define=DATA_SOURCE=mock
```

Any email with a password of 6 or more characters signs in. Without synced
fixtures the app starts with a message naming the sync command.

## Live mode

```sh
flutter run --dart-define=DATA_SOURCE=live \
  --dart-define=API_BASE_URL=http://<laptop LAN IP>:8000 \
  --dart-define=WS_URL=ws://<laptop LAN IP>:8000/ws \
  --dart-define=SUPABASE_URL=https://<project>.supabase.co \
  --dart-define=SUPABASE_ANON_KEY=<public anon key>
```

Sign-in works in live mode; the incident screens need `LiveIncidentRepository`
(task A3.1).

## Checks

```sh
flutter analyze
flutter test
OCP_CONTRACTS_DIR=<contracts> flutter test test/contracts   # fixtures from another checkout
dart run build_runner build                                  # after changing lib/data/models
```

The DTOs' `*.g.dart` files are generated and committed; never edit them by hand.
