"""Allowed incident status transitions (FR-009; architecture §5).

Every status change goes through ``check_transition`` before it is written.
"""

from __future__ import annotations

from oncallpilot_contracts.enums import IncidentStatus

ALLOWED: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    "investigating": frozenset({"awaiting_approval", "escalated", "resolved"}),
    "awaiting_approval": frozenset({"executing", "escalated", "resolved"}),
    # awaiting_approval only after STATE_DRIFT (FR-017); escalated only for a
    # rollback whose verification fails (FR-016) or a refused/late runner.
    "executing": frozenset({"verifying", "action_failed", "awaiting_approval", "escalated"}),
    "verifying": frozenset({"resolved", "action_failed", "escalated"}),
    "action_failed": frozenset({"awaiting_approval", "escalated", "resolved"}),
    "escalated": frozenset({"resolved"}),
    "resolved": frozenset(),
}


class IllegalStatusChangeError(Exception):
    pass


def check_transition(current: IncidentStatus, new: IncidentStatus) -> None:
    if new not in ALLOWED[current]:
        raise IllegalStatusChangeError(f"{current} -> {new} is not allowed (FR-009)")
