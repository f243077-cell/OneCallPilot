// `/executions/:id?incident=<id>` (MOB-011, architecture §10.2): the runner's
// steps from `execution.progress`, the structural and signal health checks,
// and the final verdict. When a rollback proposal for this execution exists,
// "Review rollback" opens it in Action Review. When the incident ended
// escalated after the execution (`runner_refused`, `runner_timeout`), the
// reason is shown and there is no rollback button.

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/models/enums.dart';
import '../../../data/models/execution.dart';
import '../../../data/models/incident.dart';
import '../../../data/models/proposal.dart';
import '../../../data/repositories/incident_repository.dart';
import '../../../shared/formatting.dart';
import '../../incident_detail/application/incident_detail_provider.dart';

/// The incident that holds an execution, for a link without `?incident=`.
final executionIncidentProvider = FutureProvider.family<String, String>((
  ref,
  executionId,
) async {
  final repository = ref.watch(incidentRepositoryProvider);
  for (final incident in await repository.listIncidents()) {
    final detail = await repository.getIncident(incident.id);
    if (detail.executions.any((e) => e.id == executionId)) return incident.id;
  }
  throw const ApiError(
    ApiErrorCode.notFound,
    status: 404,
    message: 'No such execution',
  );
}, retry: (_, _) => null);

/// Escalations that end an execution with no rollback offered (§5.7).
const runnerEscalations = {
  EscalationReason.runnerRefused,
  EscalationReason.runnerTimeout,
};

class ExecutionStatusScreen extends ConsumerWidget {
  const ExecutionStatusScreen({
    super.key,
    required this.executionId,
    this.incidentId,
  });

  final String executionId;
  final String? incidentId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final known = incidentId;
    return Scaffold(
      appBar: AppBar(title: const Text('Execution')),
      body: known != null
          ? _ForIncident(executionId: executionId, incidentId: known)
          : switch (ref.watch(executionIncidentProvider(executionId))) {
              AsyncData(:final value) => _ForIncident(
                executionId: executionId,
                incidentId: value,
              ),
              AsyncError(:final error) => _Message(_errorText(error)),
              _ => const Center(child: CircularProgressIndicator()),
            },
    );
  }
}

String _errorText(Object error) =>
    error is ApiError && error.code == ApiErrorCode.notFound
    ? 'Execution not found'
    : describeError(error);

class _ForIncident extends ConsumerWidget {
  const _ForIncident({required this.executionId, required this.incidentId});

  final String executionId;
  final String incidentId;

  @override
  Widget build(BuildContext context, WidgetRef ref) =>
      switch (ref.watch(incidentDetailProvider(incidentId))) {
        AsyncValue(:final value?) => switch (value.executions
            .where((e) => e.id == executionId)
            .firstOrNull) {
          final execution? => ExecutionStatusBody(
            incident: value,
            execution: execution,
          ),
          // Approved a moment ago: the first progress event has not arrived.
          null => const _Message('Waiting for the runner…'),
        },
        AsyncError(:final error) => _Message(_errorText(error)),
        _ => const Center(child: CircularProgressIndicator()),
      };
}

class ExecutionStatusBody extends StatelessWidget {
  const ExecutionStatusBody({
    super.key,
    required this.incident,
    required this.execution,
  });

  final IncidentDetail incident;
  final Execution execution;

  /// What the execution came to, in one line.
  static String verdict(IncidentDetail incident, Execution execution) {
    final runnerEnded =
        incident.status == IncidentStatus.escalated &&
        runnerEscalations.contains(incident.statusReason);
    if (runnerEnded) {
      return switch (incident.statusReason) {
        EscalationReason.runnerRefused =>
          'Escalated: the runner refused the action'
              '${execution.errorCode == null ? '' : ' (${execution.errorCode})'}',
        _ => 'Escalated: the runner did not answer in time',
      };
    }
    return switch (execution.status) {
      ExecutionStatus.queued => 'Queued for the runner',
      ExecutionStatus.running => 'Running',
      ExecutionStatus.aborted =>
        'Aborted before any change'
            '${execution.errorCode == null ? '' : ' (${execution.errorCode})'}',
      ExecutionStatus.failed =>
        'The action failed'
            '${execution.errorCode == null ? '' : ' (${execution.errorCode})'}',
      ExecutionStatus.succeeded => switch (execution.healthAfter?.verdict) {
        'pass' => 'Recovered: both health checks passed',
        'fail' => 'Done, but the service did not recover',
        _ => 'Done; verifying recovery…',
      },
      ExecutionStatus.unknown => 'Unknown',
    };
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final proposal = incident.proposals
        .where((p) => p.id == execution.proposalId)
        .firstOrNull;
    final runnerEnded =
        incident.status == IncidentStatus.escalated &&
        runnerEscalations.contains(incident.statusReason);
    final rollback = runnerEnded
        ? null
        : incident.proposals
              .where(
                (p) =>
                    p.kind == ProposalKind.rollback &&
                    p.parentProposalId == execution.proposalId,
              )
              .lastOrNull;
    final health = execution.healthAfter;

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
      children: [
        Text(
          proposal == null ? 'Action' : humanize(proposal.action),
          style: theme.textTheme.titleLarge,
        ),
        if (proposal != null)
          Text(
            [
              if (proposal.kind == ProposalKind.rollback) 'Rollback',
              proposal.params.entries
                  .map((e) => '${e.key}=${e.value}')
                  .join(', '),
            ].join(' · '),
          ),
        const SizedBox(height: 8),
        Text(
          verdict(incident, execution),
          key: const ValueKey('verdict'),
          style: theme.textTheme.titleMedium,
        ),
        Text('Incident: ${_incidentText(incident)}'),
        if (rollback != null)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: FilledButton.icon(
              key: const ValueKey('review-rollback'),
              icon: const Icon(Icons.undo),
              label: const Text('Review rollback'),
              onPressed: () => context.push(
                '/proposals/${rollback.id}?incident=${incident.id}',
              ),
            ),
          ),
        _Heading('Steps'),
        if (execution.steps.isEmpty) const Text('No steps yet'),
        for (final (i, step) in execution.steps.indexed)
          ListTile(
            key: ValueKey('step-$i'),
            contentPadding: EdgeInsets.zero,
            leading: _StepIcon(step.status),
            title: Text(step.step),
            subtitle: Text(step.message),
            trailing: Text(formatClock(step.at)),
          ),
        _Heading('Health checks'),
        _Check(
          key: const ValueKey('structural'),
          title: 'Structural (runner)',
          passed: health?.structural?.passed,
          details: [
            for (final c
                in health?.structural?.containers ?? <ContainerState>[])
              '${c.name}: ${humanize(c.state)} · ${humanize(c.health)}',
            for (final p in health?.structural?.probes ?? const [])
              '${p.url}: ${p.ok ? 'ok' : 'failed'}'
                  '${p.statusCode == null ? '' : ' (${p.statusCode})'}',
          ],
        ),
        _Check(
          key: const ValueKey('signals'),
          title: 'Signals (worker)',
          passed: health?.signals?.passed,
          details: [
            if (health?.signals case final signals?)
              '${signals.signals.map(humanize).join(', ')} '
                  'over ${signals.windowSeconds} s',
          ],
        ),
      ],
    );
  }

  static String _incidentText(IncidentDetail i) => switch (i.status) {
    IncidentStatus.escalated when i.statusReason != null =>
      'escalated (${humanize(i.statusReason!).toLowerCase()})',
    IncidentStatus.resolved when i.resolution != null =>
      'resolved (${humanize(i.resolution!).toLowerCase()})',
    _ => humanize(i.status).toLowerCase(),
  };
}

class _StepIcon extends StatelessWidget {
  const _StepIcon(this.status);

  final StepStatus status;

  @override
  Widget build(BuildContext context) => switch (status) {
    StepStatus.succeeded => const Icon(
      Icons.check_circle_outline,
      color: Colors.green,
    ),
    StepStatus.failed => Icon(
      Icons.cancel_outlined,
      color: Theme.of(context).colorScheme.error,
    ),
    StepStatus.running => const Icon(Icons.more_horiz),
    StepStatus.unknown => const Icon(Icons.help_outline),
  };
}

class _Check extends StatelessWidget {
  const _Check({
    super.key,
    required this.title,
    required this.passed,
    required this.details,
  });

  final String title;

  /// null: not run yet.
  final bool? passed;
  final List<String> details;

  @override
  Widget build(BuildContext context) => ListTile(
    contentPadding: EdgeInsets.zero,
    leading: switch (passed) {
      true => const Icon(Icons.check_circle_outline, color: Colors.green),
      false => Icon(
        Icons.cancel_outlined,
        color: Theme.of(context).colorScheme.error,
      ),
      null => const Icon(Icons.hourglass_empty),
    },
    title: Text(
      '$title: ${switch (passed) {
        true => 'passed',
        false => 'failed',
        null => 'pending',
      }}',
    ),
    subtitle: details.isEmpty ? null : Text(details.join('\n')),
  );
}

class _Heading extends StatelessWidget {
  const _Heading(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 20, bottom: 6),
    child: Text(text, style: Theme.of(context).textTheme.titleMedium),
  );
}

class _Message extends StatelessWidget {
  const _Message(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(padding: const EdgeInsets.all(24), child: Text(text)),
  );
}
