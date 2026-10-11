"""The runner's second gate: rate limit and cooldown (RUN-006, architecture §5.12, §7.6).

The runner keeps its own counters, independent of the backend's, so both gates
hold even if one is bypassed:

- `ocp:runner:ratelimit:{service}`: a sorted set of execution timestamps
  (member: the execution_id). At most 3 executions per service in 1800 s.
- `ocp:runner:cooldown:{service}`: a string with a 120 s TTL.

clear_cache counts under service `api` (ruled 2026-10-10).

C5 order (runner.py, check 8): the idempotency claim, then the rate limit
(`RATE_LIMITED`), then the cooldown (`COOLDOWN_ACTIVE`), then the state
fingerprint. A refusal is a signed `refused` result and nothing changes.

**Only executions that ran count.** `consume` is called after the fingerprint
check passed and right before the action's first change, so refusals,
`aborted` results (drift, validation) and dry runs never use the budget. It
adds the timestamp and sets the cooldown at once: if the runner dies mid-run,
the service still cools down. `finished` sets the cooldown again when the
action ends, so the 120 s count from the end (§7.6).

**Fail closed:** every Redis error propagates. The consumer then leaves the
entry unacknowledged and sends no result; the execution ID is already claimed,
so the redelivered entry is a duplicate that never runs, and the worker's
sweeper ends the execution with `RUNNER_TIMEOUT` (FR-024).

**Not here yet:** the cooldown exemption for rollback proposals (§7.6) waits for
the decision on contract-review-a issue 12 (the marker must be signed). Until
then a rollback is subject to the cooldown like any other action.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

RATE_LIMIT = 3
RATE_WINDOW_SECONDS = 1800
COOLDOWN_SECONDS = 120
CLEAR_CACHE_LIMITS_SERVICE = "api"


class LimitStore(Protocol):
    """The Redis commands the limits need (all in the `runner` ACL user's rules)."""

    def zcount(self, key: str, min_score: float, max_score: float) -> int: ...
    def zadd(self, key: str, member: str, score: float) -> None: ...
    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> None: ...
    def expire(self, key: str, ttl_seconds: int) -> None: ...
    def set_ex(self, key: str, ttl_seconds: int) -> None: ...
    def exists(self, key: str) -> bool: ...
    def ttl(self, key: str) -> int: ...


def limits_service(action: str, params: dict[str, str | int]) -> str:
    """The service whose limits an action counts under."""
    if action == "clear_cache":
        return CLEAR_CACHE_LIMITS_SERVICE
    return str(params["service"])


def rate_key(service: str) -> str:
    return f"ocp:runner:ratelimit:{service}"


def cooldown_key(service: str) -> str:
    return f"ocp:runner:cooldown:{service}"


@dataclass
class Limits:
    store: LimitStore
    now: Callable[[], datetime]

    def used(self, service: str) -> int:
        """Executions on the service in the last 30 minutes."""
        since = self.now().timestamp() - RATE_WINDOW_SECONDS
        return self.store.zcount(rate_key(service), since, float("inf"))

    def cooling(self, service: str) -> bool:
        return self.store.exists(cooldown_key(service))

    def refusal(self, service: str) -> tuple[str, str] | None:
        """(error_code, reason) if a gate is closed, in C5's order; else None."""
        used = self.used(service)
        if used >= RATE_LIMIT:
            return "RATE_LIMITED", (
                f"{used} executions on {service} in the last {RATE_WINDOW_SECONDS // 60} min "
                f"(limit {RATE_LIMIT})"
            )
        if self.cooling(service):
            left = self.store.ttl(cooldown_key(service))
            return "COOLDOWN_ACTIVE", f"{service} is cooling down for {left} s more"
        return None

    def consume(self, service: str, execution_id: str) -> None:
        """The execution is about to change something: count it and start the cooldown."""
        t = self.now().timestamp()
        key = rate_key(service)
        self.store.zremrangebyscore(key, float("-inf"), t - RATE_WINDOW_SECONDS)
        self.store.zadd(key, execution_id, t)
        self.store.expire(key, RATE_WINDOW_SECONDS)
        self.store.set_ex(cooldown_key(service), COOLDOWN_SECONDS)

    def finished(self, service: str) -> None:
        """The action ended: the 120 s cooldown counts from now."""
        self.store.set_ex(cooldown_key(service), COOLDOWN_SECONDS)
