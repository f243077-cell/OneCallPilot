// C3 event envelope (ws.py: EventEnvelopeBase and its events). `data` stays a
// map; its shape depends on `event` (IncidentSummary for incident.opened,
// Evidence for evidence.added, …), and readers parse the part they need.

import 'package:json_annotation/json_annotation.dart';

import 'enums.dart';

part 'ws_event.g.dart';

@JsonSerializable()
class WsEvent {
  const WsEvent({
    required this.eventId,
    required this.incidentId,
    required this.stateVersion,
    required this.ts,
    required this.event,
    required this.data,
  });

  factory WsEvent.fromJson(Map<String, dynamic> json) =>
      _$WsEventFromJson(json);

  /// Redis stream position, `<milliseconds>-<sequence>`.
  final String eventId;
  final String incidentId;

  /// The incident's state_version after the change; de-duplicates events.
  final int stateVersion;
  final DateTime ts;
  @JsonKey(unknownEnumValue: WsEventName.unknown)
  final WsEventName event;
  final Map<String, dynamic> data;
}
