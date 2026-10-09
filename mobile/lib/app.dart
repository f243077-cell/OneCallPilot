import 'package:flutter/material.dart';

/// Root widget. Becomes `MaterialApp.router` with go_router in task A1.5.
class OnCallPilotApp extends StatelessWidget {
  const OnCallPilotApp({super.key});

  @override
  Widget build(BuildContext context) {
    return const MaterialApp(
      title: 'OnCallPilot',
      home: Scaffold(body: Center(child: Text('OnCallPilot'))),
    );
  }
}
