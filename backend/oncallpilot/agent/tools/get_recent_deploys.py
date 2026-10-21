"""``get_recent_deploys`` (architecture §6.3; AI-007): the deploy ledger, read-only.

Newest first, at most 10 records, only the agent-visible C6 fields
(``DeployListItem``): ``deploy_id`` and ``image_tag`` never reach the model
(TB-010).
"""

from __future__ import annotations

from pathlib import Path

from oncallpilot_contracts.domain import DeployListItem, DeployListPayload

from oncallpilot.agent.tools.params import GetRecentDeploysParams
from oncallpilot.ledger import read_records


def get_recent_deploys(ledger: Path, params: GetRecentDeploysParams) -> DeployListPayload:
    records = [r for r in read_records(ledger) if params.service in (None, r.service)]
    records.sort(key=lambda r: r.deployed_at, reverse=True)
    return DeployListPayload(
        records=[
            DeployListItem(
                service=r.service,
                release=r.release,
                commit_sha=r.commit_sha,
                commit_message=r.commit_message,
                config_hash=r.config_hash,
                deployed_at=r.deployed_at,
                deployed_by=r.deployed_by,
                kind=r.kind,
                replicas=r.replicas,
                reason=r.reason,
            )
            for r in records[: params.limit]
        ]
    )
