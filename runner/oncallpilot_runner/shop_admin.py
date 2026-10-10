"""The store's internal admin API through cs-lb (RUN-012, architecture §2.10).

The clear_cache dry run reads `GET /internal/cache/stats`; the execute handler
(A2.4) will call `POST /internal/cache/clear`. Both carry RUNNER_ADMIN_TOKEN as
a bearer token, which is never logged.
"""

import json
import urllib.error
import urllib.request
from typing import Any

SHOP_URL = "http://cs-lb"
TIMEOUT_SECONDS = 5


class CacheStatsUnavailable(RuntimeError):
    """The stats endpoint did not answer with the expected JSON."""


def fetch_cache_stats(token: str, base_url: str = SHOP_URL) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}/internal/cache/stats",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body: Any = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise CacheStatsUnavailable(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise CacheStatsUnavailable(type(exc).__name__) from None
    if not isinstance(body, dict):
        raise CacheStatsUnavailable("the answer is not a JSON object")
    return body
