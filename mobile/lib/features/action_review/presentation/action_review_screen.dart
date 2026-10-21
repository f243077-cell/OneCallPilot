// `/proposals/:id?incident=<id>` (MOB-007, architecture §10.2): the action,
// parameters, risk tier, approval requirement, expected effect, rationale with
// `E#` chips, the dry run (would change, current state, preconditions,
// warnings), the rollback step or "Not reversible", and an expiry countdown by
// server time. Approve runs the C8 flow (MOB-008, MOB-009); Reject asks for a
// reason (MOB-010) and stays enabled whenever the proposal is pending.
//
// Approve is enabled only for a pending, unexpired proposal shown in its latest
// version while the socket is `connectedSynced` (MOB-015): an event newer than
// the incident on screen disables it until the refetch lands. StaleBanner
// says why. Reject stays enabled whenever the proposal is pending.

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../../core/errors/api_error.dart';
import '../../../data/api/ws_client.dart';
import '../../../data/models/enums.dart';
import '../../../data/models/evidence.dart';
import '../../../data/models/incident.dart';
import '../../../data/models/proposal.dart';
import '../../../data/models/ws_event.dart';
import '../../../data/repositories/incident_repository.dart';
import '../../../shared/formatting.dart';
import '../../../shared/server_clock.dart';
import '../../../shared/widgets/evidence_card.dart';
import '../../../shared/widgets/expiry_countdown.dart';
import '../../../shared/widgets/risk_badge.dart';
import '../../../shared/widgets/stale_banner.dart';
import '../../incident_detail/application/incident_detail_provider.dart';
import '../application/approval_controller.dart';
import '../application/proposal_lookup.dart';

class ActionReviewScreen extends ConsumerWidget {
  const ActionReviewScreen({
    super.key,
    required this.proposalId,
    this.incidentId,
  });

  final String proposalId;
  final String? incidentId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final known = incidentId;
    return Scaffold(
      appBar: AppBar(title: const Text('Action review')),
      body: known != null
          ? _ForIncident(proposalId: proposalId, incidentId: known)
          : switch (ref.watch(proposalIncidentProvider(proposalId))) {
              AsyncData(:final value) => _ForIncident(
                proposalId: proposalId,
                incidentId: value,
              ),
              AsyncError(:final error) => _Message(
                _notFound(error) ? 'Proposal not found' : describeError(error),
              ),
              _ => const Center(child: CircularProgressIndicator()),
            },
    );
  }
}

bool _notFound(Object error) =>
    error is ApiError && error.code == ApiErrorCode.notFound;

class _ForIncident extends ConsumerWidget {
  const _ForIncident({required this.proposalId, required this.incidentId});

  final String proposalId;
  final String incidentId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return switch (ref.watch(incidentDetailProvider(incidentId))) {
      AsyncValue(:final value?) => switch (value.proposals
          .where((p) => p.id == proposalId)
          .firstOrNull) {
        final proposal? => ActionReviewBody(
          incident: value,
          proposal: proposal,
        ),
        null => const _Message('Proposal not found'),
      },
      AsyncError(:final error) => _Message(
        _notFound(error) ? 'Proposal not found' : describeError(error),
      ),
      _ => const Center(child: CircularProgressIndicator()),
    };
  }
}

class ActionReviewBody extends ConsumerStatefulWidget {
  const ActionReviewBody({
    super.key,
    required this.incident,
    required this.proposal,
  });

  final IncidentDetail incident;
  final Proposal proposal;

  @override
  ConsumerState<ActionReviewBody> createState() => _ActionReviewBodyState();
}

class _ActionReviewBodyState extends ConsumerState<ActionReviewBody> {
  Timer? _expiry;
  StreamSubscription<WsEvent>? _events;

  /// The newest state_version announced for this incident; while it is newer
  /// than the one shown, the view is not the latest (MOB-015).
  int _newestVersion = 0;

  @override
  void initState() {
    super.initState();
    _scheduleExpiry();
    _events = ref.read(incidentRepositoryProvider).events().listen((event) {
      if (event.incidentId == widget.incident.id &&
          event.stateVersion > _newestVersion &&
          mounted) {
        setState(() => _newestVersion = event.stateVersion);
      }
    });
  }

  @override
  void didUpdateWidget(ActionReviewBody old) {
    super.didUpdateWidget(old);
    if (old.proposal.expiresAt != widget.proposal.expiresAt) _scheduleExpiry();
  }

  /// Rebuilds at the moment the proposal expires, to disable Approve.
  void _scheduleExpiry() {
    _expiry?.cancel();
    final left = widget.proposal.expiresAt.difference(
      ref.read(serverNowProvider)(),
    );
    if (left > Duration.zero) {
      _expiry = Timer(left, () {
        if (mounted) setState(() {});
      });
    }
  }

  @override
  void dispose() {
    _expiry?.cancel();
    unawaited(_events?.cancel());
    super.dispose();
  }

  Future<bool> _confirmTap() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Approve this action?'),
        content: Text(
          '${humanize(widget.proposal.action)} '
          '${formatParams(widget.proposal.params)}',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            key: const ValueKey('confirm-approve'),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Approve'),
          ),
        ],
      ),
    );
    return ok ?? false;
  }

  Future<void> _reject() async {
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => const RejectDialog(),
    );
    if (reason == null || !mounted) return;
    await ref
        .read(approvalControllerProvider(widget.proposal.id).notifier)
        .reject(widget.proposal, reason);
  }

  @override
  Widget build(BuildContext context) {
    final proposal = widget.proposal;
    final incident = widget.incident;
    final provider = approvalControllerProvider(proposal.id);
    final approval = ref.watch(provider);
    ref.listen(provider, (_, next) {
      if (next.phase == ApprovalPhase.approved && next.executionId != null) {
        context.pushReplacement(
          '/executions/${next.executionId}?incident=${incident.id}',
        );
      } else if (next.phase == ApprovalPhase.rejected) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Rejected; the incident is escalated')),
        );
        if (context.canPop()) context.pop();
      }
    });

    final theme = Theme.of(context);
    final pending = proposal.status == ProposalStatus.pending;
    final expired = !proposal.expiresAt.isAfter(ref.watch(serverNowProvider)());
    final synced =
        ref.watch(connectionStatusProvider) == ConnectionStatus.connectedSynced;
    final updating = _newestVersion > incident.stateVersion;
    final canApprove =
        pending && !expired && synced && !updating && !approval.busy;
    final canReject = pending && !approval.busy;
    final evidence = {for (final e in incident.evidence) e.ref: e};
    final dryRun = proposal.dryRunResult;

    return Column(
      children: [
        StaleBanner(
          reason: !pending
              ? null
              : updating
              ? 'This incident changed — updating'
              : expired
              ? 'This proposal expired'
              : null,
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 16, 16, 32),
            children: [
              Text(
                humanize(proposal.action),
                key: const ValueKey('action'),
                style: theme.textTheme.titleLarge,
              ),
              if (proposal.kind == ProposalKind.rollback)
                const Text('Rollback of the action that failed verification'),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  RiskBadge(proposal.riskTier),
                  Chip(
                    key: const ValueKey('requirement'),
                    avatar: Icon(
                      proposal.approvalRequirement == ApprovalRequirement.tap
                          ? Icons.touch_app_outlined
                          : Icons.fingerprint,
                      size: 18,
                    ),
                    label: Text(requirementLabel(proposal.approvalRequirement)),
                  ),
                  if (pending)
                    ExpiryCountdown(
                      proposal.expiresAt,
                      key: const ValueKey('expiry'),
                    )
                  else
                    Text(
                      humanize(proposal.status),
                      key: const ValueKey('status'),
                      style: theme.textTheme.labelLarge,
                    ),
                ],
              ),
              _Heading('Parameters'),
              for (final MapEntry(:key, :value) in proposal.params.entries)
                _Row(key, '$value'),
              _Heading('Expected effect'),
              Text(
                proposal.expectedEffect,
                key: const ValueKey('expected-effect'),
              ),
              _Heading('Why'),
              Text(proposal.rationale, key: const ValueKey('rationale')),
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                children: [
                  for (final cited in proposal.evidenceRefs)
                    ActionChip(
                      key: ValueKey('evidence-chip-$cited'),
                      label: Text(cited),
                      onPressed: switch (evidence[cited]) {
                        final e? => () => _showEvidence(context, e, incident),
                        null => null,
                      },
                    ),
                ],
              ),
              _Heading('Dry run'),
              Text(
                dryRun.predictedEffect,
                key: const ValueKey('predicted-effect'),
              ),
              const SizedBox(height: 8),
              Text('Would change', style: theme.textTheme.labelLarge),
              if (dryRun.wouldChange.isEmpty) const Text('Nothing'),
              for (final change in dryRun.wouldChange)
                _Row(change.target, change.change),
              const SizedBox(height: 8),
              Text('Current state', style: theme.textTheme.labelLarge),
              if (dryRun.current.activeRelease case final release?)
                _Row('Active release', release),
              if (dryRun.current.runningReplicas case final replicas?)
                _Row('Running replicas', '$replicas'),
              if (dryRun.current.cacheKeys case final keys?)
                _Row('Cache keys', '$keys'),
              for (final c in dryRun.current.containers)
                _Row(c.name, '${humanize(c.state)} · ${humanize(c.health)}'),
              const SizedBox(height: 8),
              Text('Preconditions', style: theme.textTheme.labelLarge),
              _Check('Rate limit', dryRun.preconditions.rateLimitOk),
              _Check('Cooldown', dryRun.preconditions.cooldownOk),
              _Check('Slots available', dryRun.preconditions.slotsAvailable),
              if (dryRun.warnings.isNotEmpty) ...[
                const SizedBox(height: 8),
                for (final warning in dryRun.warnings)
                  ListTile(
                    key: ValueKey('warning-$warning'),
                    contentPadding: EdgeInsets.zero,
                    leading: Icon(
                      Icons.warning_amber_rounded,
                      color: theme.colorScheme.error,
                    ),
                    title: Text(warning),
                  ),
              ],
              _Heading('Rollback'),
              Text(switch (proposal.rollbackStep) {
                final step? =>
                  '${humanize(step.action)} ${formatParams(step.params)}',
                null => 'Not reversible',
              }, key: const ValueKey('rollback-step')),
              const SizedBox(height: 24),
              if (approval.message case final message?)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: Text(
                    message,
                    key: const ValueKey('approval-message'),
                    style: TextStyle(color: theme.colorScheme.error),
                  ),
                ),
              if (approval.canRetry)
                Padding(
                  padding: const EdgeInsets.only(bottom: 12),
                  child: OutlinedButton.icon(
                    key: const ValueKey('retry'),
                    icon: const Icon(Icons.refresh),
                    label: const Text('Retry'),
                    onPressed: () => ref.read(provider.notifier).retry(),
                  ),
                ),
              // Not while the user confirms: nothing is waiting on the network then.
              if (approval.busy && approval.phase != ApprovalPhase.confirming)
                const LinearProgressIndicator(),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton(
                      key: const ValueKey('reject'),
                      onPressed: canReject ? _reject : null,
                      child: const Text('Reject'),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: FilledButton.icon(
                      key: const ValueKey('approve'),
                      icon: Icon(
                        proposal.approvalRequirement == ApprovalRequirement.tap
                            ? Icons.check
                            : Icons.fingerprint,
                      ),
                      label: const Text('Approve'),
                      onPressed: canApprove
                          ? () => ref
                                .read(provider.notifier)
                                .approve(proposal, confirmTap: _confirmTap)
                          : null,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

void _showEvidence(
  BuildContext context,
  Evidence evidence,
  IncidentDetail incident,
) {
  showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (_) => SafeArea(
      child: SingleChildScrollView(
        child: EvidenceCard(
          evidence: evidence,
          incidentStart: incident.openedAt,
          incidentEnd: incident.windowEnd ?? incident.resolvedAt,
        ),
      ),
    ),
  );
}

String requirementLabel(ApprovalRequirement requirement) =>
    switch (requirement) {
      ApprovalRequirement.tap => 'Tap to approve',
      ApprovalRequirement.biometric => 'Biometric approval',
      ApprovalRequirement.unknown => 'Unknown approval',
    };

/// `{service: api, replicas: 3}` → "service=api, replicas=3".
String formatParams(Map<String, dynamic> params) =>
    params.entries.map((e) => '${e.key}=${e.value}').join(', ');

/// The reason dialog of MOB-010: 3–500 characters, counted after trimming.
class RejectDialog extends StatefulWidget {
  const RejectDialog({super.key});

  @override
  State<RejectDialog> createState() => _RejectDialogState();
}

class _RejectDialogState extends State<RejectDialog> {
  final _controller = TextEditingController();
  bool _valid = false;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Reject this action'),
    content: TextField(
      key: const ValueKey('reject-reason'),
      controller: _controller,
      autofocus: true,
      maxLength: 500,
      maxLines: 3,
      decoration: const InputDecoration(
        labelText: 'Reason',
        helperText: '3 to 500 characters',
      ),
      onChanged: (text) => setState(() => _valid = validRejectReason(text)),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        key: const ValueKey('confirm-reject'),
        onPressed: _valid
            ? () => Navigator.pop(context, _controller.text.trim())
            : null,
        child: const Text('Reject'),
      ),
    ],
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

class _Row extends StatelessWidget {
  const _Row(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 2),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(flex: 2, child: Text(label)),
        Expanded(flex: 3, child: Text(value)),
      ],
    ),
  );
}

class _Check extends StatelessWidget {
  const _Check(this.label, this.ok);

  final String label;
  final bool ok;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      Icon(
        ok ? Icons.check_circle_outline : Icons.cancel_outlined,
        size: 18,
        color: ok ? Colors.green : Theme.of(context).colorScheme.error,
      ),
      const SizedBox(width: 6),
      Text(ok ? label : '$label: not met'),
    ],
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
