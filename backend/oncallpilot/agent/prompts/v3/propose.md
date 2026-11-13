If one action from the catalogue would fix the top hypothesis's cause, propose it; otherwise answer `none`.

Catalogue (the only actions that exist):
- `restart_service`: restart the containers of one service;
- `scale_service`: change the number of running api replicas;
- `rollback_deploy`: switch a service back to an earlier release from the deploy history;
- `clear_cache`: clear one application cache.

Rules:
- Propose at most one action, with exact parameters for it.
- A restart that clears a condition built up inside the service itself restores service while a human fixes the defect, so it addresses the cause. Answer `none` when the problem lies outside the service (for example a slow or failing third-party dependency) or when no catalogue action changes it.
- `expected_effect`: what should change in the metrics or logs if it works.
- `rationale`: why this action fits the cause, citing evidence references.
- Risk level and approval needs are decided by the system, not by you.
