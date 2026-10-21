import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../server_clock.dart';

/// "Expires in 14:58", by server time, ticking every second; "Expired" after.
class ExpiryCountdown extends ConsumerStatefulWidget {
  const ExpiryCountdown(this.expiresAt, {super.key, this.style});

  final DateTime expiresAt;
  final TextStyle? style;

  static String label(Duration left) {
    if (left <= Duration.zero) return 'Expired';
    final seconds = (left.inMilliseconds / 1000).ceil();
    final m = seconds ~/ 60;
    final s = (seconds % 60).toString().padLeft(2, '0');
    return 'Expires in $m:$s';
  }

  @override
  ConsumerState<ExpiryCountdown> createState() => _ExpiryCountdownState();
}

class _ExpiryCountdownState extends ConsumerState<ExpiryCountdown> {
  late final Timer _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) => setState(() {}));
  }

  @override
  void dispose() {
    _timer.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final now = ref.watch(serverNowProvider)();
    return Text(
      ExpiryCountdown.label(widget.expiresAt.difference(now)),
      style: widget.style,
    );
  }
}
