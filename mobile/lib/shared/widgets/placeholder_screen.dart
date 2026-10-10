import 'package:flutter/material.dart';

/// Stands in for a screen a later task builds, so every route resolves.
class PlaceholderScreen extends StatelessWidget {
  const PlaceholderScreen({
    super.key,
    required this.title,
    required this.task,
    this.id,
  });

  final String title;
  final String task;
  final String? id;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(title)),
    body: Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Text(
          [?id, 'This screen is built in task $task.'].join('\n\n'),
          textAlign: TextAlign.center,
        ),
      ),
    ),
  );
}
