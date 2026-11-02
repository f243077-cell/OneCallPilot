"""OpenRouter adapters (ADR-23): the ADR-13 primary model and the ADR-14
embeddings through one OpenAI-compatible API, over REST with httpx.

- The API key travels only in the ``Authorization`` header, never in a URL,
  a log line or a prompt.
- Temperature 0, ``reasoning.effort = low``, a 30 s timeout per call, and one
  retry after 2 s on 429, 5xx or a network error; then
  ``LLMUnavailableError`` (architecture §6.2). Routing requires providers
  that support every parameter sent and that do not store prompts.
- Tools use OpenAI-style function calling. Structured output uses
  ``response_format: json_schema`` with ``strict: true``; replies are always
  validated again with Pydantic: the schema only guides the model.
- Every assistant message is replayed exactly as it came back (``Msg.raw``):
  Gemini 3 models need their ``reasoning_details`` (thought signatures) in the
  next turn. A tool call the agent did not run (budget) gets an error result.
- Embeddings: ``google/gemini-embedding-001`` with ``dimensions=768``,
  L2-normalised here. OpenRouter does not pass task types to the model.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
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
from oncallpilot.agent.providers.embeddings import DIMENSIONS, TaskType, normalise

API = "https://openrouter.ai/api/v1"
CALL_TIMEOUT_SECONDS = 30.0
RETRY_DELAY_SECONDS = 2.0
EMBEDDING_MODEL = "google/gemini-embedding-001"
MAX_TOKENS = {"gather": 4096, "structured": 8192}  # reasoning tokens count too
REASONING = {"effort": "low"}
ROUTING = {"require_parameters": True, "data_collection": "deny"}
NOT_RUN = "Not run: the tool-call budget for this turn is used up."

Sleep = Callable[[float], Awaitable[None]]


class _OpenRouterHttp:
    def __init__(self, client: httpx.AsyncClient, api_key: SecretStr, sleep: Sleep) -> None:
        self._client = client
        self._key = api_key
        self._sleep = sleep

    async def post(self, path: str, body: dict[str, Any], expect: str) -> dict[str, Any]:
        """POST with one retry; ``expect`` is the key a usable answer has."""
        last: str = ""
        for attempt in range(2):
            if attempt:
                await self._sleep(RETRY_DELAY_SECONDS)
            try:
                response = await self._client.post(
                    f"{API}/{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                    timeout=CALL_TIMEOUT_SECONDS,
                )
            except httpx.HTTPError as exc:
                last = type(exc).__name__
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last = f"HTTP {response.status_code}"
                continue
            if response.status_code >= 400:
                raise LLMUnavailableError(
                    f"OpenRouter refused the request: HTTP {response.status_code}"
                )
            try:
                data: dict[str, Any] = response.json()
            except ValueError:
                last = "a reply that is not JSON"
                continue
            if not data.get(expect):
                # An upstream failure can come back as HTTP 200 with an error body.
                last = "an error from the upstream provider"
                continue
            return data
        raise LLMUnavailableError(f"OpenRouter unavailable after a retry: {last}")


def _usage(data: dict[str, Any]) -> Usage:
    usage = data.get("usage") or {}
    return Usage(int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0))


def _message(data: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = dict((data["choices"][0] or {}).get("message") or {})
    return message


def to_messages(messages: list[Msg]) -> list[dict[str, Any]]:
    """Our messages → Chat Completions messages.

    System messages are joined into one, first. Tool results go back as
    ``tool`` messages (never in the system role, SEC-005). Every tool call of
    an assistant message gets a result before the next non-tool message.
    """
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    pending: list[str] = []  # tool call ids still owed a result

    def settle() -> None:
        out.extend({"role": "tool", "tool_call_id": i, "content": NOT_RUN} for i in pending)
        pending.clear()

    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            if m.tool_call_id in pending:
                pending.remove(m.tool_call_id)
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            continue
        settle()
        if m.role == "assistant":
            if m.raw is not None:
                message = dict(m.raw)
            else:
                message = {"role": "assistant", "content": m.content}
                if m.tool_calls:
                    message["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ]
            pending.extend(str(c["id"]) for c in message.get("tool_calls") or [])
            out.append(message)
        else:
            out.append({"role": "user", "content": m.content})
    settle()
    return out


def _arguments(text: Any) -> dict[str, Any]:
    """A tool call's arguments; anything that is not a JSON object becomes {} and
    fails parameter validation in the toolbox (the call still counts)."""
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return {}
    return dict(value) if isinstance(value, dict) else {}


class OpenRouterProvider:
    name = "openrouter"

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr,
        model: str,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.model = model
        self._http = _OpenRouterHttp(client, api_key, sleep)

    def _body(self, messages: list[Msg], max_tokens: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": to_messages(messages),
            "temperature": 0,
            "max_tokens": max_tokens,
            "reasoning": REASONING,
            "provider": ROUTING,
        }

    async def step_with_tools(self, messages: list[Msg], tools: list[ToolSpec]) -> LLMTurn:
        body = self._body(messages, MAX_TOKENS["gather"])
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"
        data = await self._http.post("chat/completions", body, expect="choices")
        message = _message(data)
        calls = tuple(
            ToolCall(
                id=str(c.get("id", "")),
                name=str((c.get("function") or {}).get("name", "")),
                arguments=_arguments((c.get("function") or {}).get("arguments")),
            )
            for c in message.get("tool_calls") or []
        )
        return LLMTurn(
            tool_calls=calls,
            done=not calls,
            text=str(message.get("content") or ""),
            usage=_usage(data),
            raw=message,
        )

    async def structured(self, messages: list[Msg], schema: type[M]) -> Structured[M]:
        body = self._body(messages, MAX_TOKENS["structured"])
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": schema.model_json_schema(),
            },
        }
        data = await self._http.post("chat/completions", body, expect="choices")
        usage = _usage(data)
        message = _message(data)
        text = str(message.get("content") or "")
        try:
            return Structured(schema.model_validate_json(text), usage, raw=message)
        except ValidationError as exc:
            raise InvalidStructuredOutputError(str(exc), usage, message, text) from exc


class OpenRouterEmbeddingProvider:
    name = "openrouter"
    model = f"openrouter/{EMBEDDING_MODEL}"  # stored as runbook_chunks.embedding_model

    def __init__(
        self, client: httpx.AsyncClient, api_key: SecretStr, sleep: Sleep = asyncio.sleep
    ) -> None:
        self._http = _OpenRouterHttp(client, api_key, sleep)

    async def embed(self, texts: list[str], task: TaskType) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), 100):
            batch = texts[start : start + 100]
            data = await self._http.post(
                "embeddings",
                {
                    "model": EMBEDDING_MODEL,
                    "input": batch,
                    "dimensions": DIMENSIONS,
                    "encoding_format": "float",
                    "provider": {"data_collection": "deny"},
                },
                expect="data",
            )
            items = sorted(data["data"], key=lambda item: int(item.get("index") or 0))
            if len(items) != len(batch):
                raise LLMUnavailableError("OpenRouter returned the wrong number of embeddings")
            for item in items:
                values = [float(v) for v in item.get("embedding") or []]
                if len(values) != DIMENSIONS:
                    raise LLMUnavailableError(
                        f"expected {DIMENSIONS} dimensions, got {len(values)}"
                    )
                out.append(normalise(values))
        return out
