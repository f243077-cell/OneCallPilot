"""Bearer-token guard for the ``/internal/*`` admin endpoints (TB-001).

``CHAOS_TOKEN`` guards fault and control endpoints (chaos CLI only);
``RUNNER_ADMIN_TOKEN`` guards the cache endpoints (runner ``clear_cache``).
Fails closed: without a configured token the endpoint refuses every call.
Tokens are accepted only in the ``Authorization`` header, never in the URL.
"""

import hmac
from collections.abc import Callable

from fastapi import Header, HTTPException

MIN_TOKEN_LENGTH = 16


def bearer_guard(expected: str) -> Callable[[str | None], None]:
    def check(authorization: str | None = Header(default=None)) -> None:
        if len(expected) < MIN_TOKEN_LENGTH:
            raise HTTPException(status_code=503, detail="admin endpoint not configured")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme != "Bearer" or not hmac.compare_digest(token.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")

    return check
