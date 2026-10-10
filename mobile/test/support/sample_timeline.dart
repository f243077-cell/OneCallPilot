// A small C1 timeline written for the tests (not a copy of contracts/fixtures):
// opened → evidence → hypothesis → proposal → awaiting approval → [approve] →
// approved → executing → step → succeeded → resolved.

const incidentId = '11111111-1111-4111-8111-111111111111';
const proposalId = '22222222-2222-4222-8222-222222222222';
const executionId = '33333333-3333-4333-8333-333333333333';
final fingerprint = 'f' * 64;
final stateFingerprint = 'e' * 64;

String _at(int seconds) => DateTime.utc(
  2026,
  10,
  14,
  8,
  20,
).add(Duration(seconds: seconds)).toIso8601String();

Map<String, dynamic> _summary(String status, int version) => {
  'id': incidentId,
  'service': 'api',
  'severity': 'sev2',
  'status': status,
  'status_reason': null,
  'title': 'api error rate above baseline',
  'opened_at': _at(0),
  'resolved_at': null,
  'resolution': null,
  'state_version': version,
  'top_hypothesis': null,
  'pending_proposal_id': null,
};

final _evidence = {
  'id': '44444444-4444-4444-8444-444444444444',
  'ref': 'E1',
  'kind': 'detector_signal',
  'purpose': 'seed',
  'tool_name': null,
  'summary': 'error_rate 0.31 against a baseline of 0.01',
  'payload': {
    'service': 'api',
    'signals': [_signal],
    'observed_at': _at(0),
  },
  'suspicious_content': false,
  'created_at': _at(5),
};

final _signal = {
  'name': 'error_rate',
  'value': 0.31,
  'baseline': 0.01,
  'zscore': 9.5,
  'first_anomalous_at': _at(0),
};

final _hypothesis = {
  'id': '55555555-5555-4555-8555-555555555555',
  'rank': 1,
  'summary': 'The latest api release fails on checkout',
  'root_cause_category': 'bad_deploy',
  'confidence': 0.82,
  'confidence_initial': 0.86,
  'evidence_refs': ['E1'],
  'status': 'active',
  'reflection_notes': null,
};

Map<String, dynamic> _proposal(String status, String riskTier) => {
  'id': proposalId,
  'incident_id': incidentId,
  'kind': 'primary',
  'parent_proposal_id': null,
  'action': 'rollback_deploy',
  'params': {'service': 'api', 'target_release': '1.4.0'},
  'risk_tier': riskTier,
  'approval_requirement': riskTier == 'low' ? 'tap' : 'biometric',
  'expected_effect': 'checkout errors stop',
  'rationale': 'errors began with the 1.5.0 deploy',
  'evidence_refs': ['E1'],
  'rollback_step': null,
  'dry_run_result': {
    'would_change': [
      {'target': 'cs-api-150-1', 'change': 'stop'},
      {'target': 'cs-api-140-1', 'change': 'start'},
    ],
    'current': {
      'active_release': '1.5.0',
      'running_replicas': 1,
      'containers': [
        {'name': 'cs-api-150-1', 'state': 'running', 'health': 'healthy'},
      ],
      'cache_keys': null,
    },
    'preconditions': {
      'rate_limit_ok': true,
      'cooldown_ok': true,
      'slots_available': true,
    },
    'predicted_effect': 'api runs 1.4.0 on one replica',
    'warnings': <String>[],
    'state_fingerprint': stateFingerprint,
  },
  'dry_run_at': _at(110),
  'fingerprint': fingerprint,
  'status': status,
  'status_reason': null,
  'expires_at': _at(120 + 900),
  'created_at': _at(120),
};

Map<String, dynamic> _event(
  int version,
  int seconds,
  String name,
  Map<String, dynamic> data,
) => {
  'type': 'event',
  'event_id': '1760430000000-$version',
  'incident_id': incidentId,
  'state_version': version,
  'ts': _at(seconds),
  'event': name,
  'data': data,
};

Map<String, dynamic> _step(
  Map<String, dynamic> event, {
  int delayMs = 100,
  bool awaitApproval = false,
}) => {'delay_ms': delayMs, 'await_approval': awaitApproval, 'event': event};

/// The timeline as raw JSON; [riskTier] `low` makes the proposal tap-approved.
Map<String, dynamic> sampleTimeline({String riskTier = 'medium'}) {
  final top = {
    'summary': _hypothesis['summary'],
    'confidence': _hypothesis['confidence'],
    'root_cause_category': _hypothesis['root_cause_category'],
  };
  final step = {
    'at': _at(140),
    'step': 'start cs-api-140-1',
    'status': 'succeeded',
    'message': 'started',
  };
  return {
    'name': 'sample',
    'description': 'A test timeline',
    'incident_id': incidentId,
    'steps': [
      _step(_event(1, 0, 'incident.opened', _summary('investigating', 1))),
      _step(
        _event(2, 5, 'evidence.added', {
          ..._evidence,
          'payload_truncated': false,
        }),
      ),
      _step(
        _event(3, 60, 'hypothesis.updated', {
          'hypotheses': [_hypothesis],
        }),
      ),
      _step(_event(4, 120, 'proposal.created', _proposal('pending', riskTier))),
      _step(
        _event(5, 121, 'incident.updated', {
          'status': 'awaiting_approval',
          'status_reason': null,
          'severity': 'sev2',
        }),
      ),
      _step(
        _event(6, 130, 'proposal.updated', {
          'proposal_id': proposalId,
          'status': 'approved',
          'status_reason': null,
          'superseded_by': null,
        }),
        delayMs: 0,
        awaitApproval: true,
      ),
      _step(
        _event(7, 131, 'incident.updated', {
          'status': 'executing',
          'status_reason': null,
          'severity': 'sev2',
        }),
      ),
      _step(
        _event(8, 140, 'execution.progress', {
          'execution_id': executionId,
          'proposal_id': proposalId,
          'step': step['step'],
          'status': 'succeeded',
          'message': 'started',
        }),
      ),
      _step(
        _event(9, 150, 'proposal.updated', {
          'proposal_id': proposalId,
          'status': 'succeeded',
          'status_reason': null,
          'superseded_by': null,
        }),
      ),
      _step(
        _event(10, 200, 'incident.resolved', {
          'resolution': 'action_succeeded',
          'resolved_at': _at(200),
        }),
      ),
    ],
    'final': {
      ..._summary('resolved', 10),
      'resolved_at': _at(200),
      'resolution': 'action_succeeded',
      'top_hypothesis': top,
      'window_start': _at(-600),
      'window_end': _at(200),
      'trigger': {
        'source': 'detector',
        'signals': [_signal],
        'observed_at': _at(0),
        'updates': <Object>[],
      },
      'evidence': [_evidence],
      'hypotheses': [_hypothesis],
      'proposals': [_proposal('succeeded', riskTier)],
      'executions': [
        {
          'id': executionId,
          'proposal_id': proposalId,
          'status': 'succeeded',
          'steps': [step],
          'error_code': null,
          'health_after': null,
          'started_at': _at(131),
          'finished_at': _at(150),
        },
      ],
      'agent_meta': <String, Object>{},
    },
  };
}
