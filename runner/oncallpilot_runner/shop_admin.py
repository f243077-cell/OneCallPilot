"""The store's internal admin API through cs-lb (RUN-012, architecture §2.10).

The clear_cache dry run reads `GET /internal/cache/stats`; the execute handler
calls `POST /internal/cache/clear {cache}`. Both carry RUNNER_ADMIN_TOKEN as a
bearer token, which is never logged. The clear answers `{"cache", "cleared"}`,
where `cleared` says whether the key existed (a yes or no, not a count), so the
handler reads the stats just before it to report how many keys it cleared.
"""

import json
import urllib.error
import urllib.request
from typing import Any

SHOP_URL = "http://cs-lb"
TIMEOUT_SECONDS = 5


class CacheStatsUnavailable(RuntimeError):
    """The stats endpoint did not answer with the expected JSON."""


class CacheClearFailed(RuntimeError):
    """The clear endpoint did not confirm the clear."""


def _call(
    path: str, token: str, base_url: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base_url}{path}", data=data, headers=headers, method="GET" if data is None else "POST"
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        answer: Any = json.loads(response.read().decode("utf-8"))
    if not isinstance(answer, dict):
        raise ValueError("the answer is not a JSON object")
    return answer


def fetch_cache_stats(token: str, base_url: str = SHOP_URL) -> dict[str, Any]:
    try:
        return _call("/internal/cache/stats", token, base_url)
    except urllib.error.HTTPError as exc:
        raise CacheStatsUnavailable(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CacheStatsUnavailable(type(exc).__name__) from None
    except ValueError as exc:
        raise CacheStatsUnavailable(
            "the answer is not a JSON object" if "JSON object" in str(exc) else type(exc).__name__
        ) from None


def clear_cache(token: str, cache: str, base_url: str = SHOP_URL) -> bool:
    """True if the cache key existed and was deleted, False if it was already empty."""
    try:
        answer = _call("/internal/cache/clear", token, base_url, {"cache": cache})
    except urllib.error.HTTPError as exc:
        raise CacheClearFailed(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise CacheClearFailed(type(exc).__name__) from None
    if answer.get("cache") != cache or not isinstance(answer.get("cleared"), bool):
        raise CacheClearFailed("unexpected answer")
    return bool(answer["cleared"])
