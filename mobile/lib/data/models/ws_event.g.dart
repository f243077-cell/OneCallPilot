// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'ws_event.dart';

// **************************************************************************
// JsonSerializableGenerator
// **************************************************************************

WsEvent _$WsEventFromJson(Map<String, dynamic> json) => WsEvent(
  eventId: json['event_id'] as String,
  incidentId: json['incident_id'] as String,
  stateVersion: (json['state_version'] as num).toInt(),
  ts: DateTime.parse(json['ts'] as String),
  event: $enumDecode(
    _$WsEventNameEnumMap,
    json['event'],
    unknownValue: WsEventName.unknown,
  ),
  data: json['data'] as Map<String, dynamic>,
);

const _$WsEventNameEnumMap = {
  WsEventName.incidentOpened: 'incident.opened',
  WsEventName.incidentUpdated: 'incident.updated',
  WsEventName.evidenceAdded: 'evidence.added',
  WsEventName.hypothesisUpdated: 'hypothesis.updated',
  WsEventName.proposalCreated: 'proposal.created',
  WsEventName.proposalUpdated: 'proposal.updated',
  WsEventName.executionProgress: 'execution.progress',
  WsEventName.incidentResolved: 'incident.resolved',
  WsEventName.unknown: 'unknown',
};
