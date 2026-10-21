"""Claude adapter (ADR-13 fallback; AI-021), through the official ``anthropic`` SDK.

- The key comes from ``ANTHROPIC_API_KEY`` and is only ever given to the SDK
  client; it never reaches a URL, a log line or a prompt.
- A 30 s timeout per call and one retry on 429, 5xx or a network error (the
  SDK's own retry); then ``LLMUnavailableError`` (architecture §6.2).
- Current Claude models always think and take no sampling parameters, so
  "temperature 0" cannot be requested; ``effort`` is set low to stay inside
  the agent's 90 s budget. Every assistant turn is replayed to the API
  exactly as it came back (``Msg.raw``), thinking blocks included.
- Gather turns use tool use with ``tool_choice: auto``. A tool call the agent
  did not run (budget) is answered with an error ``tool_result``.
- Structured replies: the prompt carries the JSON Schema and the reply is
  validated with Pydantic, like every other provider. Constrained decoding
  (``output_config.format``) is not used because it cannot express the
  free-form ``params`` objects of ``ProposalOut`` and ``DisproofPlanOut``.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
from pydantic import SecretStr, ValidationError

from oncallpilot.agent.providers.base import (
    InvalidStructuredOutputError,
    LLMTurn,
    LLMUnavailableError,
    M,
    Msg,
    Structured,
    ToolCall,
    ToolSpec,
    Usage,
)

CALL_TIMEOUT_SECONDS = 30.0
MAX_TOKENS = 16_000
EFFORT = "low"
NOT_RUN = "Not run: the tool-call budget for this turn is used up."


def anthropic_client(api_key: SecretStr) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key=api_key.get_secret_value(), timeout=CALL_TIMEOUT_SECONDS, max_retries=1
    )


def _blocks(content: Any) -> list[dict[str, Any]]:
    """Response content blocks as plain dicts, unchanged, for replay."""
    return [b.to_dict() if hasattr(b, "to_dict") else dict(b) for b in content]


def _text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text").strip()


def _usage(response: Any) -> Usage:
    usage = getattr(response, "usage", None)
    return Usage(
        int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0)
    )


def to_messages(
    messages: list[Msg], suffixes: dict[str, str] | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Our messages → (system, Messages API turns).

    ``suffixes`` maps a user message's text to the schema instruction it was
    first sent with, so a replayed prompt is byte-for-byte what the model saw.

    Consecutive turns of one role are merged; every ``tool_use`` gets a
    ``tool_result`` in the next user turn (an error result if it was not run),
    and tool results come first in that turn.
    """
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    turns: list[dict[str, Any]] = []
    pending: list[str] = []  # tool_use ids still owed a result

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    def settle() -> None:
        if pending:
            add("user", [])
            owed = [
                {"type": "tool_result", "tool_use_id": i, "content": NOT_RUN, "is_error": True}
                for i in pending
            ]
            turns[-1]["content"] = [*turns[-1]["content"], *owed]
            pending.clear()
        if turns and turns[-1]["role"] == "user":
            content = turns[-1]["content"]
            results = [b for b in content if b.get("type") == "tool_result"]
            turns[-1]["content"] = results + [b for b in content if b.get("type") != "tool_result"]

    for m in messages:
        if m.role == "system":
            continue
        if m.role == "assistant":
            settle()
            if m.raw is not None:
                blocks = list(m.raw)
            else:
                blocks = [{"type": "text", "text": m.content}] if m.content else []
                blocks += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in m.tool_calls
                ]
            pending.extend(b["id"] for b in blocks if b.get("type") == "tool_use")
            add("assistant", blocks or [{"type": "text", "text": "(no reply)"}])
        elif m.role == "tool":
            if m.tool_call_id in pending:
                pending.remove(m.tool_call_id)
            add("user", [{"type": "tool_result", "tool_use_id": m.tool_call_id,
                          "content": m.content}])  # fmt: skip
        else:
            suffix = (suffixes or {}).get(m.content)
            text = f"{m.content}\n\n{suffix}" if suffix else m.content
            add("user", [{"type": "text", "text": text}])
    settle()
    return system, turns


def _json_object(text: str) -> str:
    """The JSON object in a reply, without a Markdown fence around it."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start : end + 1] if start != -1 and end > start else stripped


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, effort: str = EFFORT) -> None:
        self.model = model
        self._client = client
        self._effort = effort
        self._suffixes: dict[str, str] = {}  # prompt text → its schema instruction

    async def _create(self, messages: list[Msg], **extra: Any) -> Any:
        system, turns = to_messages(messages, self._suffixes)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": turns,
            "output_config": {"effort": self._effort},
            **extra,
        }
        if system:
            kwargs["system"] = system
        try:
            return await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            # 4xx after no retry, or 429/5xx/network after the SDK's one retry.
            raise LLMUnavailableError(f"Claude unavailable: {type(exc).__name__}") from exc

    async def step_with_tools(self, messages: list[Msg], tools: list[ToolSpec]) -> LLMTurn:
        response = await self._create(
            messages,
            tools=[
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ],
            tool_choice={"type": "auto"},
        )
        blocks = _blocks(response.content)
        calls = tuple(
            ToolCall(id=str(b["id"]), name=str(b["name"]), arguments=dict(b.get("input") or {}))
            for b in blocks
            if b.get("type") == "tool_use"
        )
        return LLMTurn(
            tool_calls=calls, done=not calls, text=_text(blocks), usage=_usage(response), raw=blocks
        )

    async def structured(self, messages: list[Msg], schema: type[M]) -> Structured[M]:
        instruction = (
            "Reply with only one JSON object that validates against this JSON Schema, "
            "with no other text:\n" + json.dumps(schema.model_json_schema(), separators=(",", ":"))
        )
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        if last_user is not None:
            self._suffixes.setdefault(last_user.content, instruction)
        response = await self._create(messages)
        blocks = _blocks(response.content)
        usage = _usage(response)
        text = _text(blocks)
        if getattr(response, "stop_reason", None) == "refusal":
            raise InvalidStructuredOutputError("the model declined to answer", usage, blocks, text)
        try:
            return Structured(schema.model_validate_json(_json_object(text)), usage, raw=blocks)
        except ValidationError as exc:
            raise InvalidStructuredOutputError(str(exc), usage, blocks, text) from exc
