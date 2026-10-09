import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/app.dart';

void main() {
  testWidgets('app starts and shows its name', (tester) async {
    await tester.pumpWidget(const OnCallPilotApp());

    expect(find.text('OnCallPilot'), findsOneWidget);
  });
}
