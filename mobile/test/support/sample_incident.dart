// An IncidentDetail written for the screen tests (not a copy of
// contracts/fixtures): one evidence item of every kind, two active hypotheses
// and one dropped one, and a pending proposal.

const detailId = '66666666-6666-4666-8666-666666666666';
const pendingId = '77777777-7777-4777-8777-777777777777';
final openedAt = DateTime.utc(2026, 10, 10, 11, 48);

String _t(int seconds) =>
    openedAt.add(Duration(seconds: seconds)).toIso8601String();

Map<String, dynamic> evidenceJson(
  String ref,
  String kind,
  Map<String, dynamic> payload, {
  String purpose = 'gather',
  String? tool,
  String summary = 'evidence summary',
}) => {
  'id': '00000000-0000-4000-8000-0000000000${ref.substring(1).padLeft(2, '0')}',
  'ref': ref,
  'kind': kind,
  'purpose': purpose,
  'tool_name': tool,
  'summary': summary,
  'payload': payload,
  'suspicious_content': false,
  'created_at': _t(10),
};

final signalJson = {
  'name': 'error_rate',
  'value': 0.31,
  'baseline': 0.004,
  'zscore': 11.2,
  'first_anomalous_at': _t(-30),
};

final allEvidence = [
  evidenceJson('E1', 'detector_signal', {
    'service': 'api',
    'signals': [signalJson],
    'observed_at': _t(0),
  }, purpose: 'seed'),
  evidenceJson(
    'E2',
    'log_query',
    {
      'lines': [
        {
          'ts': _t(5),
          'level': 'INFO',
          'msg': 'GET /products 200',
          'exc_type': null,
        },
        {
          'ts': _t(6),
          'level': 'ERROR',
          'msg': 'checkout failed: tuple index out of range',
          'exc_type': 'IndexError',
        },
        {
          'ts': _t(7),
          'level': 'CRITICAL',
          'msg': 'worker exited',
          'exc_type': null,
        },
      ],
      'total_count': 41,
      'top_exception_types': [
        {'exc_type': 'IndexError', 'count': 39},
      ],
    },
    tool: 'query_logs',
    summary: '41 error lines on /checkout',
  ),
  evidenceJson(
    'E3',
    'metric_query',
    {
      'template': 'error_rate',
      'service': 'api',
      'step_seconds': 15,
      'points': [
        for (var i = 0; i < 6; i++)
          {'ts': _t(-60 + i * 15), 'value': i < 3 ? 0.004 : 0.31},
      ],
      'baseline_mean': 0.004,
      'peak': 0.31,
      'pct_change': 7650.0,
      'change_point_at': _t(-15),
      'note': null,
    },
    tool: 'query_metrics',
    summary: 'error rate rose 77 times',
  ),
  evidenceJson(
    'E4',
    'deploy_list',
    {
      'records': [
        {
          'service': 'api',
          'release': '1.5.0',
          'commit_sha': 'e56a282e7523212fea82c73f9157b41d5ba1743b',
          'commit_message':
              'checkout: compute totals with new pricing rounding',
          'config_hash': 'a' * 64,
          'deployed_at': _t(-120),
          'deployed_by': 'ci',
          'kind': 'deploy',
        },
      ],
    },
    tool: 'get_recent_deploys',
    summary: '1.5.0 deployed 2 min before',
  ),
  evidenceJson(
    'E5',
    'service_health',
    {
      'service': 'api',
      'containers': [
        {
          'name': 'cs-api-150-1',
          'release': '1.5.0',
          'state': 'running',
          'health': 'healthy',
          'restart_count': 0,
          'oom_killed': false,
          'exit_code': null,
          'started_at': _t(-120),
        },
      ],
      'probe': {
        'url': 'http://cs-api-150-1:8000/healthz',
        'ok': true,
        'status_code': 200,
        'latency_ms': 4.2,
        'error': null,
      },
    },
    tool: 'get_service_health',
    summary: 'one healthy replica',
  ),
  evidenceJson(
    'E6',
    'runbook_hit',
    {
      'chunks': [
        {
          'source': 'runbooks/api-errors.md',
          'heading': 'Errors after a deploy',
          'content': 'Compare the error start with the last deploy time.',
          'similarity': 0.82,
        },
      ],
    },
    tool: 'search_runbooks',
    summary: 'runbook: errors after a deploy',
  ),
  evidenceJson(
    'E7',
    'log_query',
    {'lines': <Object>[], 'total_count': 0, 'top_exception_types': <Object>[]},
    purpose: 'disproof',
    tool: 'query_logs',
    summary: 'no errors on 1.4.0',
  ),
];

Map<String, dynamic> hypothesisJson(
  int rank,
  String status,
  List<String> refs, {
  String category = 'bad_deploy',
  double confidence = 0.8,
}) => {
  'id': '88888888-8888-4888-8888-00000000000$rank',
  'rank': rank,
  'summary': 'Hypothesis number $rank about the checkout errors',
  'root_cause_category': category,
  'confidence': confidence,
  'confidence_initial': confidence,
  'evidence_refs': refs,
  'status': status,
  'reflection_notes': null,
};

Map<String, dynamic> proposalJson() => {
  'id': pendingId,
  'incident_id': detailId,
  'kind': 'primary',
  'parent_proposal_id': null,
  'action': 'rollback_deploy',
  'params': {'service': 'api', 'target_release': '1.4.0'},
  'risk_tier': 'medium',
  'approval_requirement': 'biometric',
  'expected_effect': 'checkout errors stop',
  'rationale': 'errors began with the 1.5.0 deploy',
  'evidence_refs': ['E4'],
  'rollback_step': null,
  'dry_run_result': {
    'would_change': <Object>[],
    'current': {
      'active_release': '1.5.0',
      'running_replicas': 1,
      'containers': <Object>[],
      'cache_keys': null,
    },
    'preconditions': {
      'rate_limit_ok': true,
      'cooldown_ok': true,
      'slots_available': true,
    },
    'predicted_effect': 'api runs 1.4.0',
    'warnings': <Object>[],
    'state_fingerprint': 'e' * 64,
  },
  'dry_run_at': _t(100),
  'fingerprint': 'f' * 64,
  'status': 'pending',
  'status_reason': null,
  'expires_at': _t(1000),
  'created_at': _t(100),
};

/// A full IncidentDetail; [overrides] replace top-level keys.
Map<String, dynamic> incidentJson({
  Map<String, dynamic> overrides = const {},
}) => {
  'id': detailId,
  'service': 'api',
  'severity': 'sev1',
  'status': 'awaiting_approval',
  'status_reason': null,
  'title': 'Checkout errors on api',
  'opened_at': _t(0),
  'resolved_at': null,
  'resolution': null,
  'state_version': 12,
  'top_hypothesis': null,
  'pending_proposal_id': pendingId,
  'window_start': _t(-600),
  'window_end': null,
  'trigger': {
    'source': 'detector',
    'signals': [signalJson],
    'observed_at': _t(0),
    'updates': <Object>[],
  },
  'evidence': allEvidence,
  'hypotheses': [
    hypothesisJson(
      2,
      'active',
      ['E2'],
      category: 'application_bug',
      confidence: 0.3,
    ),
    hypothesisJson(1, 'active', ['E2', 'E3', 'E4'], confidence: 0.86),
    hypothesisJson(
      3,
      'dropped',
      ['E5'],
      category: 'traffic_surge',
      confidence: 0.1,
    ),
  ],
  'proposals': [proposalJson()],
  'executions': <Object>[],
  'agent_meta': <String, Object>{},
  ...overrides,
};
