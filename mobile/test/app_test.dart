import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/app.dart';

import 'support/test_app.dart';

void main() {
  testWidgets('the app starts at login in mock mode', (tester) async {
    final app = await TestApp.create();
    await tester.pumpWidget(app.widget);
    await tester.pumpAndSettle();

    expect(find.text('OnCallPilot'), findsOneWidget);
    expect(find.text('Mock mode: fixture data, no network'), findsOneWidget);
  });

  testWidgets('a startup error is shown, not a crash', (tester) async {
    await tester.pumpWidget(
      const StartupErrorApp(message: 'No fixture timelines'),
    );
    expect(find.text('No fixture timelines'), findsOneWidget);
  });
}
