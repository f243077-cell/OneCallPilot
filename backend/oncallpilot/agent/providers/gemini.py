"""Gemini adapters (ADR-13 primary LLM, ADR-14 embeddings), over the REST API with httpx.

- The API key travels only in the ``x-goog-api-key`` header, never in a URL,
  a log line or a prompt.
- Temperature 0, a 30 s timeout per call, and one retry after 2 s on 429,
  5xx or a network error; then ``LLMUnavailableError`` (architecture §6.2).
- Tools use function calling; structured output uses JSON mode with a
  response schema. Gemini accepts an OpenAPI subset, so Pydantic's JSON
  Schema is converted (``to_gemini_schema``). Replies are always validated
  again with Pydantic: the schema only guides the model.
- Embeddings: ``gemini-embedding-001`` with ``outputDimensionality=768``,
  L2-normalised here (the API normalises only the full 3072-d output).
"""

from __future__ import annotations

import asyncio
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

API = "https://generativelanguage.googleapis.com/v1beta"
CALL_TIMEOUT_SECONDS = 30.0
RETRY_DELAY_SECONDS = 2.0
EMBEDDING_MODEL = "gemini-embedding-001"
MAX_TOKENS = {"gather": 1024, "structured": 2048}

_DROP = {"title", "default", "additionalProperties", "$schema", "examples", "const"}


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic JSON Schema → Gemini's OpenAPI subset: inline ``$ref``s, turn
    ``anyOf [X, null]`` into a nullable X, and drop unsupported keywords."""
    defs = schema.get("$defs", {})

    def convert(node: Any) -> Any:
        if isinstance(node, list):
            return [convert(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return convert(defs[node["$ref"].split("/")[-1]])
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            nullable = len(options) < len(node["anyOf"])
            merged: dict[str, Any] = (
                convert(options[0])
                if len(options) == 1
                else {"anyOf": [convert(o) for o in options]}
            )
            if nullable:
                merged["nullable"] = True
            if "description" in node:
                merged["description"] = node["description"]
            return merged
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _DROP or key == "$defs":
                continue
            if key == "properties":
                out[key] = {k: convert(v) for k, v in value.items()}
            else:
                out[key] = convert(value)
        if "const" in node:
            out["enum"] = [node["const"]]
            out.setdefault("type", "string")
        if out.get("type") == "object" and "properties" not in out:
            # A free-form object (tool params): Gemini needs at least one property.
            out["properties"] = {"_": {"type": "string", "nullable": True}}
        return out

    return convert(schema)  # type: ignore[no-any-return]


Sleep = Callable[[float], Awaitable[None]]


class _GeminiHttp:
    def __init__(self, client: httpx.AsyncClient, api_key: SecretStr, sleep: Sleep) -> None:
        self._client = client
        self._key = api_key
        self._sleep = sleep

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        last: str = ""
        for attempt in range(2):
            if attempt:
                await self._sleep(RETRY_DELAY_SECONDS)
            try:
                response = await self._client.post(
                    f"{API}/{path}",
                    json=body,
                    headers={"x-goog-api-key": self._key.get_secret_value()},
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
                    f"Gemini refused the request: HTTP {response.status_code}"
                )
            data: dict[str, Any] = response.json()
            return data
        raise LLMUnavailableError(f"Gemini unavailable after a retry: {last}")


def _usage(data: dict[str, Any]) -> Usage:
    meta = data.get("usageMetadata") or {}
    return Usage(int(meta.get("promptTokenCount") or 0), int(meta.get("candidatesTokenCount") or 0))


def _parts(data: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = data.get("candidates") or []
    if not candidates:
        return []
    return list((candidates[0].get("content") or {}).get("parts") or [])


def to_contents(messages: list[Msg]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Our messages → (systemInstruction, contents). Tool results go back as
    functionResponse parts (never in the system role, SEC-005)."""
    system = [m.content for m in messages if m.role == "system"]
    names: dict[str, str] = {}
    contents: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "assistant":
            parts: list[dict[str, Any]] = [{"text": m.content}] if m.content else []
            for call in m.tool_calls:
                names[call.id] = call.name
                parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
        elif m.role == "tool":
            name = names.get(m.tool_call_id or "", "tool")
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {"functionResponse": {"name": name, "response": {"content": m.content}}}
                    ],
                }
            )
        else:
            contents.append({"role": "user", "parts": [{"text": m.content}]})
    instruction = {"parts": [{"text": "\n\n".join(system)}]} if system else None
    return instruction, contents


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr,
        model: str,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.model = model
        self._http = _GeminiHttp(client, api_key, sleep)
        self._turn = 0

    def _body(self, messages: list[Msg], max_tokens: int) -> dict[str, Any]:
        instruction, contents = to_contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens},
        }
        if instruction:
            body["systemInstruction"] = instruction
        return body

    async def step_with_tools(self, messages: list[Msg], tools: list[ToolSpec]) -> LLMTurn:
        body = self._body(messages, MAX_TOKENS["gather"])
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "parameters": to_gemini_schema(t.parameters),
                    }
                    for t in tools
                ]
            }
        ]
        body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        data = await self._http.post(f"models/{self.model}:generateContent", body)
        self._turn += 1
        calls: list[ToolCall] = []
        texts: list[str] = []
        for i, part in enumerate(_parts(data)):
            if "functionCall" in part:
                call = part["functionCall"]
                calls.append(
                    ToolCall(
                        id=f"g{self._turn}-{i}",
                        name=str(call.get("name", "")),
                        arguments=dict(call.get("args") or {}),
                    )
                )
            elif part.get("text"):
                texts.append(str(part["text"]))
        return LLMTurn(
            tool_calls=tuple(calls), done=not calls, text="\n".join(texts), usage=_usage(data)
        )

    async def structured(self, messages: list[Msg], schema: type[M]) -> Structured[M]:
        body = self._body(messages, MAX_TOKENS["structured"])
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["generationConfig"]["responseSchema"] = to_gemini_schema(schema.model_json_schema())
        data = await self._http.post(f"models/{self.model}:generateContent", body)
        usage = _usage(data)
        text = "".join(str(p.get("text", "")) for p in _parts(data))
        try:
            return Structured(schema.model_validate_json(text), usage)
        except ValidationError as exc:
            raise InvalidStructuredOutputError(str(exc), usage) from exc


class GeminiEmbeddingProvider:
    name = "gemini"
    model = EMBEDDING_MODEL

    def __init__(
        self, client: httpx.AsyncClient, api_key: SecretStr, sleep: Sleep = asyncio.sleep
    ) -> None:
        self._http = _GeminiHttp(client, api_key, sleep)

    async def embed(self, texts: list[str], task: TaskType) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), 100):
            batch = texts[start : start + 100]
            data = await self._http.post(
                f"models/{EMBEDDING_MODEL}:batchEmbedContents",
                {
                    "requests": [
                        {
                            "model": f"models/{EMBEDDING_MODEL}",
                            "content": {"parts": [{"text": text}]},
                            "taskType": task,
                            "outputDimensionality": DIMENSIONS,
                        }
                        for text in batch
                    ]
                },
            )
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(batch):
                raise LLMUnavailableError("Gemini returned the wrong number of embeddings")
            for item in embeddings:
                values = [float(v) for v in item.get("values") or []]
                if len(values) != DIMENSIONS:
                    raise LLMUnavailableError(
                        f"expected {DIMENSIONS} dimensions, got {len(values)}"
                    )
                out.append(normalise(values))
        return out
