"""The state fingerprint (architecture §7.5, C8 §2; RUN-007, RUN-008).

SHA-256 over exactly three things:

- the service's active release, from the deploy ledger;
- its desired replica count, from the ledger;
- the sorted names of the action's target containers.

Container state, health, restart counts and start times are left out on
purpose, so a crash-looping service does not look like drift. A redis restart
has no ledger values (null, null); clear_cache has no containers either, so its
fingerprint never changes.

Only the runner computes and compares it (the dry run, then again before an
execute), so the byte encoding is the runner's: C5's canonical JSON of
`{"active_release", "desired_replicas", "target_containers"}`, lower-case hex.
"""

import hashlib
from collections.abc import Iterable

from oncallpilot_runner.signing import canonical_json


def state_fingerprint(
    active_release: str | None, desired_replicas: int | None, target_containers: Iterable[str]
) -> str:
    body = {
        "active_release": active_release,
        "desired_replicas": desired_replicas,
        "target_containers": sorted(target_containers),
    }
    return hashlib.sha256(canonical_json(body)).hexdigest()
