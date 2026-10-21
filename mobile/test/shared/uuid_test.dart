import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:oncallpilot/shared/uuid.dart';
import 'package:oncallpilot/shared/widgets/expiry_countdown.dart';

void main() {
  test('uuidV4 makes distinct version-4 UUIDs', () {
    final pattern = RegExp(
      r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    );
    final random = Random(1);
    final keys = {for (var i = 0; i < 100; i++) uuidV4(random)};
    expect(keys, hasLength(100));
    expect(keys.every(pattern.hasMatch), isTrue);
  });

  test('the expiry countdown rounds up and ends at "Expired"', () {
    expect(
      ExpiryCountdown.label(const Duration(minutes: 14, seconds: 58)),
      'Expires in 14:58',
    );
    expect(
      ExpiryCountdown.label(const Duration(milliseconds: 200)),
      'Expires in 0:01',
    );
    expect(ExpiryCountdown.label(Duration.zero), 'Expired');
    expect(ExpiryCountdown.label(const Duration(seconds: -5)), 'Expired');
  });
}
