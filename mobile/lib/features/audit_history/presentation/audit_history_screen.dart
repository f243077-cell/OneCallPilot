// `/audit?incident=<id>` (MOB-012, architecture §10.2): a timeline of
// proposals, approvals and rejections (who, when, reason), executions and
// outcomes, newest first; one incident's when opened from it, with a switch to
// every incident. Payloads come from the server already redacted.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/models/ops.dart';
import '../../../shared/formatting.dart';
import '../../auth/application/auth_session.dart';
import '../application/audit_provider.dart';

/// The events the history shows; agent internals (LLM calls, evidence,
/// hypotheses) stay out of it.
const shownEvents = {
  'incident.opened',
  'incident.status_changed',
  'proposal.created',
  'proposal.rejected_by_validator',
  'proposal.expired',
  'proposal.superseded',
  'approval.approved',
  'approval.rejected',
  'execution.queued',
  'execution.started',
  'execution.finished',
  'health.verified',
  'incident.resolved',
  'settings.updated',
};

class AuditHistoryScreen extends ConsumerWidget {
  const AuditHistoryScreen({super.key, this.incidentId});

  final String? incidentId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final provider = auditProvider(incidentId);
    final me = ref.watch(authStateProvider).user?.id;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Audit history'),
        actions: [
          if (incidentId != null)
            TextButton(
              key: const ValueKey('all-incidents'),
              onPressed: () => context.replace('/audit'),
              child: const Text('All incidents'),
            ),
        ],
      ),
      body: switch (ref.watch(provider)) {
        AsyncData(:final value) => RefreshIndicator(
          onRefresh: () => ref.refresh(provider.future),
          child: _Timeline(
            value.where((e) => shownEvents.contains(e.event)).toList(),
            me: me,
            scoped: incidentId != null,
          ),
        ),
        AsyncError(:final error) => Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(describeError(error)),
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: () => ref.invalidate(provider),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
        _ => const Center(child: CircularProgressIndicator()),
      },
    );
  }
}

class _Timeline extends StatelessWidget {
  const _Timeline(this.entries, {required this.me, required this.scoped});

  final List<AuditEntry> entries;
  final String? me;
  final bool scoped;

  @override
  Widget build(BuildContext context) {
    if (entries.isEmpty) {
      return ListView(
        children: const [
          Padding(padding: EdgeInsets.all(24), child: Text('No entries yet')),
        ],
      );
    }
    return ListView.separated(
      itemCount: entries.length,
      separatorBuilder: (_, _) => const Divider(height: 1),
      itemBuilder: (context, i) {
        final e = entries[i];
        final detail = describeAudit(e);
        return ListTile(
          key: ValueKey('audit-${e.id}'),
          leading: Icon(_icon(e.event)),
          title: Text(auditTitle(e.event)),
          subtitle: Text(
            [
              '${actorLabel(e.actor, me: me)} · ${formatDateTime(e.createdAt)}',
              ?detail,
            ].join('\n'),
          ),
          isThreeLine: detail != null,
          onTap: scoped || e.incidentId == null
              ? null
              : () => context.push('/incidents/${e.incidentId}'),
        );
      },
    );
  }

  static IconData _icon(String event) => switch (event) {
    'approval.approved' => Icons.verified_outlined,
    'approval.rejected' => Icons.block,
    'proposal.created' => Icons.fact_check_outlined,
    'execution.queued' ||
    'execution.started' ||
    'execution.finished' => Icons.play_circle_outline,
    'health.verified' => Icons.monitor_heart_outlined,
    'incident.resolved' => Icons.check_circle_outline,
    'settings.updated' => Icons.tune,
    _ => Icons.history,
  };
}

String auditTitle(String event) => switch (event) {
  'incident.opened' => 'Incident opened',
  'incident.status_changed' => 'Status changed',
  'proposal.created' => 'Action proposed',
  'proposal.rejected_by_validator' => 'Proposal refused by the validator',
  'proposal.expired' => 'Proposal expired',
  'proposal.superseded' => 'Proposal replaced',
  'approval.approved' => 'Approved',
  'approval.rejected' => 'Rejected',
  'execution.queued' => 'Execution queued',
  'execution.started' => 'Execution started',
  'execution.finished' => 'Execution finished',
  'health.verified' => 'Recovery checked',
  'incident.resolved' => 'Incident resolved',
  'settings.updated' => 'Settings changed',
  _ => event,
};

/// `user:<uuid>` → "You" or "A user"; the services by name.
String actorLabel(String actor, {String? me}) => switch (actor) {
  'agent' => 'Agent',
  'detector' => 'Detector',
  'runner' => 'Runner',
  'system' => 'System',
  _ when me != null && actor == 'user:$me' => 'You',
  _ when actor.startsWith('user:') => 'A user',
  _ => actor,
};

/// The one line of detail an entry carries, if any: the action, the reason,
/// the outcome.
String? describeAudit(AuditEntry e) {
  final p = e.payload;
  String? text(String key) => p[key] is String ? p[key] as String : null;
  final parts = <String>[
    if (text('action') case final action?)
      [
        action.replaceAll('_', ' '),
        if (p['params'] case final Map<String, dynamic> params)
          params.entries.map((x) => '${x.key}=${x.value}').join(', '),
      ].join(' '),
    if (text('reason') case final reason?) 'Reason: $reason',
    if (text('auth_method') case final method?) 'Confirmed by $method',
    if (text('status') case final status?)
      'Status: ${status.replaceAll('_', ' ')}',
    if (text('status_reason') case final reason?) reason.replaceAll('_', ' '),
    if (text('resolution') case final resolution?)
      resolution.replaceAll('_', ' '),
    if (text('verdict') case final verdict?) 'Verdict: $verdict',
  ];
  return parts.isEmpty ? null : parts.join(' · ');
}
