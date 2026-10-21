"""A per-user, in-process rate limit (``/log-tail``: 1 request per 2 s, API-006).

backend-api is one process, so a dictionary is enough; entries older than
the interval are dropped as they are checked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID


@dataclass
class PerUserInterval:
    interval: timedelta = timedelta(seconds=2)
    _last: dict[UUID, datetime] = field(default_factory=dict)

    def retry_after(self, user_id: UUID, now: datetime) -> int | None:
        """None (and the request is counted) when allowed; else seconds to wait."""
        last = self._last.get(user_id)
        if last is not None and now - last < self.interval:
            return max(1, math.ceil((self.interval - (now - last)).total_seconds()))
        self._last = {u: t for u, t in self._last.items() if now - t < self.interval}
        self._last[user_id] = now
        return None
