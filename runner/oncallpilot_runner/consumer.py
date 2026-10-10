"""Consumes ocp:runner:requests in consumer group `runner` (RUN-001, NFR-005).

- The group is created at start-up if missing (from the start of the stream).
- Crash recovery, as for the worker (architecture §5.2, v1.4): at start-up the
  runner re-reads its own pending entries (`XREADGROUP … 0`; the consumer name
  is the container hostname, which a restart keeps), and while running it
  takes over entries another consumer left idle for more than 300 s with
  `XAUTOCLAIM`. Idempotency (`SET NX`) makes a redelivered execute harmless.
- An entry is acknowledged after its result is written, so a crash in between
  repeats the result, never loses it.
- Nothing escapes the loop: a Redis error waits and retries without
  acknowledging; an unexpected error answers with a refusal (fail closed).
"""

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from redis.exceptions import RedisError

from oncallpilot_runner.checks import Accepted, Checker, Decision, Dropped, Duplicate, Refused
from oncallpilot_runner.streams import Streams

log = logging.getLogger("runner.consumer")

REQUESTS_STREAM = "ocp:runner:requests"
REQUESTS_GROUP = "runner"
RESULTS_STREAM = "ocp:runner:results"
RECLAIM_IDLE_MS = 300_000
BLOCK_MS = 5_000
BATCH = 10
HEARTBEAT = Path("/tmp/runner-heartbeat")


class Consumer:
    def __init__(
        self,
        client: Streams,
        checker: Checker,
        consumer_name: str,
        *,
        heartbeat: Path | None = HEARTBEAT,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.checker = checker
        self.name = consumer_name
        self.heartbeat = heartbeat
        self.sleep = sleep

    def ensure_group(self) -> None:
        self.client.create_group(REQUESTS_STREAM, REQUESTS_GROUP)

    def recover(self) -> int:
        """Entries this consumer read before a restart and never acknowledged."""
        handled = 0
        while True:
            entries = self.client.read_group(
                REQUESTS_GROUP, self.name, REQUESTS_STREAM, "0", BATCH, None
            )
            if not entries:
                return handled
            for entry_id, fields in entries:
                self.handle(entry_id, fields)
                handled += 1

    def reclaim(self) -> int:
        """Entries another consumer left idle for more than 300 s."""
        entries = self.client.autoclaim(
            REQUESTS_STREAM, REQUESTS_GROUP, self.name, RECLAIM_IDLE_MS, BATCH
        )
        for entry_id, fields in entries:
            self.handle(entry_id, fields)
        return len(entries)

    def poll(self) -> int:
        """New entries, waiting up to 5 s for one."""
        entries = self.client.read_group(
            REQUESTS_GROUP, self.name, REQUESTS_STREAM, ">", BATCH, BLOCK_MS
        )
        for entry_id, fields in entries:
            self.handle(entry_id, fields)
        self.beat()
        return len(entries)

    def handle(self, entry_id: str, fields: dict[str, str]) -> Decision:
        try:
            decision = self.checker.check(fields)
        except Exception:  # a bug in a check must still fail closed, never execute
            log.exception("check failed unexpectedly", extra={"entry_id": entry_id})
            decision = self.checker.refuse_unexpected(fields)
        if isinstance(decision, Refused):
            self.client.add(
                RESULTS_STREAM,
                {"msg": json.dumps(decision.result, separators=(",", ":"), ensure_ascii=False)},
            )
        self.client.ack(REQUESTS_STREAM, REQUESTS_GROUP, entry_id)
        _log(entry_id, decision)
        return decision

    def beat(self) -> None:
        if self.heartbeat is not None:
            self.heartbeat.write_text(str(time.time()), encoding="utf-8")

    def run_forever(self) -> None:
        backoff = 1.0
        recovered = False
        while True:
            try:
                if not recovered:
                    self.ensure_group()
                    count = self.recover()
                    log.info(f"consuming as {self.name}; {count} pending entries re-read")
                    recovered = True
                self.reclaim()
                self.poll()
                backoff = 1.0
            except RedisError as exc:
                log.warning(
                    f"Redis unavailable ({type(exc).__name__}); retrying in {backoff:.0f} s"
                )
                self.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


def _log(entry_id: str, decision: Decision) -> None:
    extra: dict[str, Any] = {"entry_id": entry_id, "decision": type(decision).__name__.lower()}
    if isinstance(decision, Accepted):
        r = decision.request
        extra |= {"request_id": r.request_id, "execution_id": r.execution_id, "action": r.action}
        # Nothing runs yet: dry-run and execute handlers arrive in tasks A2.3 and A2.4.
        log.info(f"{r.type} accepted; no handler runs in this build", extra=extra)
    elif isinstance(decision, Refused):
        result = decision.result
        extra |= {
            "request_id": result["request_id"],
            "execution_id": result["execution_id"],
            "error_code": decision.error_code,
        }
        log.warning(f"refused: {decision.reason}", extra=extra)
    elif isinstance(decision, Duplicate):
        extra["execution_id"] = decision.execution_id
        log.info("duplicate execute acknowledged and ignored", extra=extra)
    elif isinstance(decision, Dropped):
        log.warning(f"dropped without a result: {decision.reason}", extra=extra)
