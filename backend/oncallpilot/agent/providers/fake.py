"""``FakeProvider``: scripted LLM turns for tests and CI (CI never calls a real LLM).

- Gather turns come from a list (then "done"), or from a function of the turn
  number (for example a model that asks for tools forever).
- Structured replies are queued per schema name. A reply is a ``dict``
  (validated against the schema, so an invalid dict exercises the repair
  path), a ``BaseException`` to raise, or ``INVALID_JSON``.
- ``delay`` seconds before every call, through an injectable ``sleep``.
- A structured call with nothing scripted raises ``AssertionError`` (a test
  that forgot a reply), or ``unscripted`` when given: the worker's development
  mode passes ``LLMUnavailableError`` so every investigation escalates
  ``llm_unavailable``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import ValidationError

from oncallpilot.agent.providers.base import (
    InvalidStructuredOutputError,
    LLMTurn,
    M,
    Msg,
    Structured,
    ToolSpec,
    Usage,
)

INVALID_JSON = object()
"""A structured reply that is not JSON at all."""

TurnScript = Sequence[LLMTurn] | Callable[[int], LLMTurn]


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        turns: TurnScript = (),
        structured: dict[str, list[Any]] | None = None,
        delay: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        model: str = "fake-1",
        unscripted: BaseException | None = None,
    ) -> None:
        self.model = model
        self._unscripted = unscripted
        self._turns = turns
        self._structured = {k: list(v) for k, v in (structured or {}).items()}
        self._delay = delay
        self._sleep = sleep
        self.calls: list[str] = []
        self.messages: list[list[Msg]] = []
        self._turn = 0

    async def _wait(self) -> None:
        if self._delay:
            await self._sleep(self._delay)

    async def step_with_tools(self, messages: list[Msg], tools: list[ToolSpec]) -> LLMTurn:
        await self._wait()
        self.calls.append("step")
        self.messages.append(list(messages))
        number = self._turn
        self._turn += 1
        if callable(self._turns):
            return self._turns(number)
        if number < len(self._turns):
            return self._turns[number]
        return LLMTurn(done=True, usage=Usage(10, 2))

    async def structured(self, messages: list[Msg], schema: type[M]) -> Structured[M]:
        await self._wait()
        self.calls.append(schema.__name__)
        self.messages.append(list(messages))
        queue = self._structured.get(schema.__name__) or []
        if not queue:
            if self._unscripted is not None:
                raise self._unscripted
            raise AssertionError(f"FakeProvider has no scripted {schema.__name__} reply")
        reply = queue.pop(0)
        usage = Usage(100, 20)
        if isinstance(reply, BaseException):
            raise reply
        if reply is INVALID_JSON:
            raise InvalidStructuredOutputError("reply is not valid JSON", usage)
        try:
            return Structured(schema.model_validate(reply), usage)
        except ValidationError as exc:
            raise InvalidStructuredOutputError(str(exc), usage) from exc
