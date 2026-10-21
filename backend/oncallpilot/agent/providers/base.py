"""The ``LLMProvider`` interface (architecture §6.7).

Agent code talks only to this protocol. Temperature is always 0; each adapter
sets max output tokens per call type, a 30 s timeout per call, and one retry
on 429/5xx with a 2 s backoff, then raises ``LLMUnavailableError``.

``structured`` validates the reply against the given model. When the reply
does not validate it raises ``InvalidStructuredOutputError`` with the
validation error; the agent then sends exactly one repair message (AI-020).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel

Role = Literal["system", "user", "assistant", "tool"]

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class Msg:
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema of the tool's parameter model


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMTurn:
    """One gather turn: tool calls to run, and/or the model saying it is done."""

    tool_calls: tuple[ToolCall, ...] = ()
    done: bool = False
    text: str = ""
    usage: Usage = field(default_factory=Usage)


class LLMUnavailableError(Exception):
    """The provider failed after its retry (timeout, 429, 5xx, network)."""


class InvalidStructuredOutputError(Exception):
    """The reply did not validate; ``error`` is the message for the repair turn."""

    def __init__(self, error: str, usage: Usage | None = None) -> None:
        super().__init__(error)
        self.error = error
        self.usage = usage or Usage()


@dataclass(frozen=True)
class Structured[T]:
    value: T
    usage: Usage


class LLMProvider(Protocol):
    name: str  # "gemini" | "anthropic" | "fake"
    model: str

    async def step_with_tools(self, messages: list[Msg], tools: list[ToolSpec]) -> LLMTurn: ...

    async def structured(self, messages: list[Msg], schema: type[M]) -> Structured[M]: ...
