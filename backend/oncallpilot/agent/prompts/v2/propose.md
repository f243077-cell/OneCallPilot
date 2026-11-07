If one action from the catalogue would fix the top hypothesis's cause, propose it; otherwise answer `none`.

Catalogue (the only actions that exist):
- `restart_service`: restart the containers of one service;
- `scale_service`: change the number of running api replicas;
- `rollback_deploy`: switch a service back to an earlier release from the deploy history;
- `clear_cache`: clear one application cache.

Rules:
- Propose at most one action, with exact parameters for it.
- Answer `none` when no catalogue action addresses the cause (for example a problem in a third-party dependency, or one that a restart would only hide).
- `expected_effect`: what should change in the metrics or logs if it works.
- `rationale`: why this action fits the cause, citing evidence references.
- Risk level and approval needs are decided by the system, not by you.
