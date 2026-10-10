import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../formatting.dart';

/// "12 min ago", updated every few seconds while on screen (MOB-003).
class TimeSince extends ConsumerStatefulWidget {
  const TimeSince(this.since, {super.key, this.style, this.suffix = ' ago'});

  final DateTime since;
  final TextStyle? style;
  final String suffix;

  static const refresh = Duration(seconds: 5);

  @override
  ConsumerState<TimeSince> createState() => _TimeSinceState();
}

class _TimeSinceState extends ConsumerState<TimeSince> {
  late final Timer _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(TimeSince.refresh, (_) => setState(() {}));
  }

  @override
  void dispose() {
    _timer.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final now = ref.watch(clockProvider)();
    final age = formatAge(now.difference(widget.since));
    return Text(
      age == 'just now' ? age : '$age${widget.suffix}',
      style: widget.style,
    );
  }
}
