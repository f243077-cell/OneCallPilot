"""Consumes ocp:runner:requests in consumer group `runner` (RUN-001, NFR-005).

- The group is created at start-up if missing (from the start of the stream).
- Crash recovery, as for the worker (architecture §5.2, v1.4): at start-up the
  runner re-reads its own pending entries (`XREADGROUP … 0`; the consumer name
  is the container hostname, which a restart keeps), and while running it
  takes over entries another consumer left idle for more than 300 s with
  `XAUTOCLAIM`. Idempotency (`SET NX`) makes a redelivered execute harmless.
- An entry is acknowledged after its result is written, so a crash in between
  repeats the result, never loses it.
- An accepted dry run is answered with its preview, signed (RUN-007,
  dry_run.py). If the state cannot be read it is refused (fail closed).
- An accepted execute (its ID already claimed) runs in execute.py: a signed
  `progress` result per step, then one signed `execution_result`. A bug during
  the execution still ends in a `failed` result, because something may already
  have changed. The final result is retried on a Redis error before the entry
  is acknowledged; a progress result that cannot be published is only logged.
- Nothing escapes the loop: a Redis error waits and retries without
  acknowledging; an unexpected error answers with a refusal (fail closed).
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from redis.exceptions import RedisError

from oncallpilot_runner.checks import Accepted, Checker, Decision, Dropped, Duplicate, Refused
from oncallpilot_runner.dry_run import DryRunner, DryRunUnavailable
from oncallpilot_runner.execute import Executor, Outcome
from oncallpilot_runner.request import Request
from oncallpilot_runner.results import dry_run_ok, execution_result, progress
from oncallpilot_runner.streams import Streams

log = logging.getLogger("runner.consumer")

REQUESTS_STREAM = "ocp:runner:requests"
REQUESTS_GROUP = "runner"
RESULTS_STREAM = "ocp:runner:results"
RECLAIM_IDLE_MS = 300_000
BLOCK_MS = 5_000
BATCH = 10
HEARTBEAT = Path("/tmp/runner-heartbeat")


@dataclass(frozen=True)
class Previewed:
    """A dry run answered with its preview."""

    request: Request
    result: dict[str, Any]


@dataclass(frozen=True)
class Executed:
    """An execute that ran: its outcome and the signed final result."""

    request: Request
    outcome: Outcome
    result: dict[str, Any]


Handled = Decision | Previewed | Executed
PUBLISH_RETRIES = 5


class Consumer:
    def __init__(
        self,
        client: Streams,
        checker: Checker,
        consumer_name: str,
        *,
        dry_runner: DryRunner | None = None,
        executor: Executor | None = None,
        heartbeat: Path | None = HEARTBEAT,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.checker = checker
        self.dry_runner = dry_runner
        self.executor = executor
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

    def handle(self, entry_id: str, fields: dict[str, str]) -> Handled:
        decision: Handled
        try:
            decision = self.checker.check(fields)
            if isinstance(decision, Accepted) and decision.request.type == "dry_run":
                decision = self._dry_run(decision.request)
        except RedisError:
            raise  # not acknowledged: retried after the wait in run_forever
        except Exception:  # a bug must still fail closed, never execute
            log.exception("handling failed unexpectedly", extra={"entry_id": entry_id})
            decision = self.checker.refuse_unexpected(fields)
        if isinstance(decision, Accepted) and decision.request.type == "execute" and self.executor:
            decision = self._execute(decision.request)
        if isinstance(decision, Refused | Previewed):
            self._publish(decision.result)
        elif isinstance(decision, Executed):
            self._publish(decision.result, retries=PUBLISH_RETRIES)
        self.client.ack(REQUESTS_STREAM, REQUESTS_GROUP, entry_id)
        _log(entry_id, decision)
        return decision

    def _dry_run(self, request: Request) -> Previewed | Refused | Duplicate:
        if self.dry_runner is None:
            return self.checker.refuse_request(request, "VALIDATION_ERROR", "no dry runner")
        try:
            preview = self.dry_runner.run(request)
        except DryRunUnavailable as exc:
            return self.checker.refuse_request(
                request, "VALIDATION_ERROR", f"state unreadable: {exc}"
            )
        result = dry_run_ok(
            request_id=request.request_id,
            dry_run=preview,
            now=self.checker.now(),
            link_key=self.checker.keys.link,
        )
        return Previewed(request, result)

    def _execute(self, request: Request) -> Executed:
        assert self.executor is not None and request.execution_id is not None
        execution_id = request.execution_id

        def emit(name: str, status: str, message: str) -> None:
            step = progress(
                request_id=request.request_id, execution_id=execution_id, name=name,
                status=status, message=message, now=self.checker.now(),
                link_key=self.checker.keys.link,
            )  # fmt: skip
            try:
                self._publish(step)
            except RedisError:
                log.warning(f"progress not published: {name}",
                            extra={"execution_id": execution_id})  # fmt: skip
            self.beat()

        try:
            outcome = self.executor.execute(request, emit)
        except Exception:  # a bug after the claim: answer failed, never stay silent
            log.exception("execution failed unexpectedly", extra={"execution_id": execution_id})
            outcome = Outcome("failed", reason="internal error during the execution")
        result = execution_result(
            request_id=request.request_id, execution_id=execution_id, status=outcome.status,
            error_code=outcome.error_code, structural=outcome.structural,
            captured=outcome.captured, now=self.checker.now(), link_key=self.checker.keys.link,
        )  # fmt: skip
        return Executed(request, outcome, result)

    def _publish(self, result: dict[str, Any], *, retries: int = 0) -> None:
        message = {"msg": json.dumps(result, separators=(",", ":"), ensure_ascii=False)}
        wait = 1.0
        for attempt in range(retries + 1):
            try:
                self.client.add(RESULTS_STREAM, message)
                return
            except RedisError:
                if attempt == retries:
                    raise
                log.warning(f"result not published; retrying in {wait:.0f} s")
                self.sleep(wait)
                wait *= 2

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


def _log(entry_id: str, decision: Handled) -> None:
    extra: dict[str, Any] = {"entry_id": entry_id, "decision": type(decision).__name__.lower()}
    if isinstance(decision, Accepted):
        r = decision.request
        extra |= {"request_id": r.request_id, "execution_id": r.execution_id, "action": r.action}
        # Nothing executes yet: the execute handlers arrive in task A2.4.
        log.info(f"{r.type} accepted; no execute handler runs in this build", extra=extra)
    elif isinstance(decision, Executed):
        r, o = decision.request, decision.outcome
        extra |= {"request_id": r.request_id, "execution_id": r.execution_id, "action": r.action,
                  "error_code": o.error_code}  # fmt: skip
        level = logging.INFO if o.status == "succeeded" else logging.WARNING
        log.log(level, f"execute {o.status}" + (f": {o.reason}" if o.reason else ""), extra=extra)
    elif isinstance(decision, Previewed):
        r, dry_run = decision.request, decision.result["dry_run"]
        extra |= {
            "request_id": r.request_id,
            "action": r.action,
            "state_fingerprint": dry_run["state_fingerprint"],
        }
        log.info(f"dry run answered: {len(dry_run['would_change'])} changes", extra=extra)
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
