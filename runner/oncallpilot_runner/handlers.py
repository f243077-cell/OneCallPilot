"""The actions the runner has handlers for (RUN-003, §7.2).

A CI test fails if this set differs from the catalogue's enabled actions. Their
dry runs are in dry_run.py (A2.3); the execute bodies arrive in task A2.4, and
until then an accepted execute changes nothing. `run_migration_rollback` is never here
(CLAUDE.md §6.4).
"""

REGISTERED: frozenset[str] = frozenset(
    {"restart_service", "scale_service", "rollback_deploy", "clear_cache"}
)
