// The live log tail of Incident Detail (MOB-006, API-006): polls
// `/incidents/{id}/log-tail` every 5 s while this screen is the visible route
// and the app is in the foreground; pauses when another screen covers it and
// stops when it is closed. Lines are redacted by the server and shown as data.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/models/enums.dart';
import '../../../data/models/evidence_payloads.dart';
import '../../../data/repositories/incident_repository.dart';
import '../../../shared/formatting.dart';

class LiveLogTail extends ConsumerStatefulWidget {
  const LiveLogTail({super.key, required this.incidentId});

  final String incidentId;

  static const interval = Duration(seconds: 5);
  static const keep = 50;

  @override
  ConsumerState<LiveLogTail> createState() => _LiveLogTailState();
}

class _LiveLogTailState extends ConsumerState<LiveLogTail>
    with WidgetsBindingObserver {
  Timer? _timer;
  DateTime? _until;
  final _lines = <LogLine>[];
  String? _error;
  bool _busy = false;
  bool _foreground = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _timer = Timer.periodic(LiveLogTail.interval, (_) => unawaited(_poll()));
    WidgetsBinding.instance.addPostFrameCallback((_) => unawaited(_poll()));
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _foreground = state == AppLifecycleState.resumed;
  }

  @override
  void dispose() {
    _timer?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// Only while this route is on top and the app is in the foreground.
  bool get _visible =>
      mounted && _foreground && (ModalRoute.of(context)?.isCurrent ?? true);

  Future<void> _poll() async {
    if (_busy || !_visible) return;
    _busy = true;
    try {
      final tail = await ref
          .read(incidentRepositoryProvider)
          .logTail(widget.incidentId, since: _until);
      if (!mounted) return;
      setState(() {
        _until = tail.until;
        _error = null;
        _lines.addAll(tail.lines);
        if (_lines.length > LiveLogTail.keep) {
          _lines.removeRange(0, _lines.length - LiveLogTail.keep);
        }
      });
    } on ApiError catch (e) {
      if (mounted) setState(() => _error = e.userMessage);
    } on NetworkError catch (e) {
      if (mounted) setState(() => _error = describeError(e));
    } finally {
      _busy = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final mono = theme.textTheme.bodySmall?.copyWith(fontFamily: 'monospace');
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_error case final error?)
            Text(
              error,
              key: const ValueKey('log-tail-error'),
              style: TextStyle(color: theme.colorScheme.error),
            ),
          if (_lines.isEmpty)
            Text('No new log lines', style: theme.textTheme.bodySmall),
          for (final line in _lines.reversed.take(20))
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 1),
              child: Text(
                '${formatClock(line.ts)} ${_level(line.level)} ${line.msg}',
                style: mono?.copyWith(
                  color:
                      line.level == LogLevel.error ||
                          line.level == LogLevel.critical
                      ? theme.colorScheme.error
                      : null,
                ),
              ),
            ),
        ],
      ),
    );
  }

  static String _level(LogLevel level) => level.name.toUpperCase();
}
