"""The actions the runner has handlers for (RUN-003, §7.2).

A CI test fails if this set differs from the catalogue's enabled actions. The
handler bodies (dry run, execute) arrive in tasks A2.3 and A2.4; until then an
accepted request changes nothing. `run_migration_rollback` is never here
(CLAUDE.md §6.4).
"""

REGISTERED: frozenset[str] = frozenset(
    {"restart_service", "scale_service", "rollback_deploy", "clear_cache"}
)
