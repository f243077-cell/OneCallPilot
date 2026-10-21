"""FakeRunnerConsumer: answers ``ocp:runner:requests`` like the runner would, for
integration tests (CLAUDE.md §7). It verifies each request's signature with
the key for its type and replies with a signed result; nothing else."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from oncallpilot_contracts.runner import (
    REQUESTS_STREAM,
    RESULTS_STREAM,
    STREAM_FIELD,
    RunnerRequest,
)
from oncallpilot_contracts.signing import verify
from redis.asyncio import Redis

from tests.runner_replies import ACTION_KEY, LINK_KEY, dry_run_reply

Reply = Callable[[RunnerRequest], dict[str, Any] | None]


def ok_dry_run(request: RunnerRequest) -> dict[str, Any] | None:
    return dry_run_reply(str(request.request_id))


class FakeRunnerConsumer:
    def __init__(self, redis: Redis, reply: Reply = ok_dry_run) -> None:
        self.redis = redis
        self.reply = reply
        self.requests: list[RunnerRequest] = []
        self.bad_signatures = 0

    async def run(self) -> None:
        cursor = "0-0"
        while True:
            batch: Any = await self.redis.xread({REQUESTS_STREAM: cursor}, count=10, block=100)
            for _, entries in batch or []:
                for entry_id, fields in entries:
                    cursor = entry_id
                    raw = json.loads(fields[STREAM_FIELD])
                    key = LINK_KEY if raw.get("type") == "dry_run" else ACTION_KEY
                    if not verify(raw, key):
                        self.bad_signatures += 1
                        continue
                    request = RunnerRequest.model_validate(raw)
                    self.requests.append(request)
                    answer = self.reply(request)
                    if answer is not None:
                        await self.redis.xadd(RESULTS_STREAM, {STREAM_FIELD: json.dumps(answer)})


@asynccontextmanager
async def fake_runner(redis: Redis, reply: Reply = ok_dry_run) -> AsyncIterator[FakeRunnerConsumer]:
    consumer = FakeRunnerConsumer(redis, reply)
    task = asyncio.create_task(consumer.run())
    try:
        yield consumer
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
