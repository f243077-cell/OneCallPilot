"""Job result retention (scenario 1, architecture §11.3).

While enabled, the worker keeps 1 MiB of job results in memory every second and
never releases them, so its memory grows until the container's limit kills it.

The switch is a marker file in the container's writable layer:
- an OOM kill (SIGKILL) leaves it in place, so after ``restart: on-failure`` the
  worker grows again;
- a graceful stop (SIGTERM, as ``restart_service(worker)`` and ``chaos reset`` send)
  runs ``on_shutdown()``, which removes it.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger("chaosshop.worker")

CHUNK_BYTES = 1 << 20
INTERVAL_SECONDS = 1.0


class ResultRetention:
    def __init__(
        self,
        marker: Path,
        *,
        chunk_bytes: int = CHUNK_BYTES,
        interval: float = INTERVAL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._marker = marker
        self._chunk_bytes = chunk_bytes
        self._interval = interval
        self._sleep = sleep
        self._held: list[bytes] = []

    @property
    def enabled(self) -> bool:
        return self._marker.exists()

    @property
    def held_bytes(self) -> int:
        return len(self._held) * self._chunk_bytes

    def enable(self) -> None:
        self._marker.parent.mkdir(parents=True, exist_ok=True)
        self._marker.touch()

    def disable(self) -> None:
        self._marker.unlink(missing_ok=True)
        self._held.clear()

    def grow_once(self) -> None:
        if self.enabled:
            # Filled, not zeroed, so the pages really count as resident memory.
            self._held.append(b"\x01" * self._chunk_bytes)

    async def run(self) -> None:
        while True:
            self.grow_once()
            await self._sleep(self._interval)

    def on_shutdown(self) -> None:
        self.disable()
