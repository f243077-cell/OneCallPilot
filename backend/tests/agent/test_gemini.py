"""Gemini adapters against a fake HTTP server (ADR-13, ADR-14; CI never calls a real LLM)."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from oncallpilot.agent.providers.base import (
    InvalidStructuredOutputError,
    LLMUnavailableError,
    Msg,
    ToolCall,
)
from oncallpilot.agent.providers.gemini import (
    GeminiEmbeddingProvider,
    GeminiProvider,
    to_contents,
    to_gemini_schema,
)
from oncallpilot.agent.schemas import (
    DisproofPlanOut,
    HypothesisSetOut,
    ProposalOut,
    ReflectionOut,
)
from oncallpilot.agent.tools.registry import read_tool_specs

KEY = SecretStr("test-key-not-real")


class Server:
    def __init__(self, *replies: httpx.Response) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.replies.pop(0)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def body(self, i: int = 0) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.requests[i].content)
        return data


def reply(parts: list[dict[str, Any]], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "candidates": [{"content": {"role": "model", "parts": parts}}],
            "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 30},
        },
    )


async def provider(server: Server) -> tuple[GeminiProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return GeminiProvider(client, KEY, "gemini-test-flash", sleep=server.sleep), client


MESSAGES = [Msg("system", "rules"), Msg("user", "investigate E1")]


async def test_tool_turn_request_and_parsing() -> None:
    server = Server(
        reply(
            [
                {"text": "checking"},
                {"functionCall": {"name": "query_logs", "args": {"service": "api"}}},
            ]
        )
    )
    gemini, client = await provider(server)
    async with client:
        turn = await gemini.step_with_tools(MESSAGES, read_tool_specs())
    request = server.requests[0]
    assert request.headers["x-goog-api-key"] == "test-key-not-real"
    assert "key=" not in str(request.url) and "test-key" not in str(request.url)
    assert str(request.url).endswith("/models/gemini-test-flash:generateContent")
    body = server.body()
    assert body["generationConfig"]["temperature"] == 0
    assert body["systemInstruction"] == {"parts": [{"text": "rules"}]}
    declared = {d["name"] for d in body["tools"][0]["functionDeclarations"]}
    assert declared == {s.name for s in read_tool_specs()}
    assert "$ref" not in json.dumps(body) and "$defs" not in json.dumps(body)
    assert turn.tool_calls == (
        ToolCall(id="g1-1", name="query_logs", arguments={"service": "api"}),
    )
    assert (turn.done, turn.text, turn.usage.input_tokens) == (False, "checking", 120)


async def test_a_turn_without_calls_is_done() -> None:
    server = Server(reply([{"text": "enough evidence"}]))
    gemini, client = await provider(server)
    async with client:
        turn = await gemini.step_with_tools(MESSAGES, read_tool_specs())
    assert turn.done and not turn.tool_calls


async def test_structured_output_validates_with_pydantic() -> None:
    good = {
        "action": "none",
        "params": {},
        "expected_effect": "x",
        "rationale": "y",
        "evidence_refs": ["E1"],
    }
    server = Server(reply([{"text": json.dumps(good)}]), reply([{"text": '{"action": "reboot"}'}]))
    gemini, client = await provider(server)
    async with client:
        result = await gemini.structured(MESSAGES, ProposalOut)
        assert result.value.action == "none"
        with pytest.raises(InvalidStructuredOutputError) as caught:
            await gemini.structured(MESSAGES, ProposalOut)
    assert caught.value.usage.output_tokens == 30
    config = server.body()["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["type"] == "object"


async def test_retry_once_on_429_then_succeed() -> None:
    server = Server(httpx.Response(429), reply([{"text": "ok"}]))
    gemini, client = await provider(server)
    async with client:
        turn = await gemini.step_with_tools(MESSAGES, [])
    assert turn.text == "ok" and server.sleeps == [2.0] and len(server.requests) == 2


@pytest.mark.parametrize("first,second", [(500, 503), (429, 429)])
async def test_two_failures_make_the_llm_unavailable(first: int, second: int) -> None:
    server = Server(httpx.Response(first), httpx.Response(second))
    gemini, client = await provider(server)
    async with client:
        with pytest.raises(LLMUnavailableError):
            await gemini.step_with_tools(MESSAGES, [])
    assert len(server.requests) == 2


async def test_a_400_is_not_retried() -> None:
    server = Server(httpx.Response(400, json={"error": {"message": "bad"}}))
    gemini, client = await provider(server)
    async with client:
        with pytest.raises(LLMUnavailableError, match="400"):
            await gemini.step_with_tools(MESSAGES, [])
    assert len(server.requests) == 1


async def test_network_errors_are_retried() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("slow")
        return reply([{"text": "ok"}])

    async def no_sleep(seconds: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as client:
        gemini = GeminiProvider(client, KEY, "m", sleep=no_sleep)
        assert (await gemini.step_with_tools(MESSAGES, [])).text == "ok"


def test_tool_results_go_back_as_function_responses_never_system() -> None:
    call = ToolCall(id="g1-0", name="query_logs", arguments={"service": "api"})
    instruction, contents = to_contents(
        [
            Msg("system", "rules"),
            Msg("user", "go"),
            Msg("assistant", "", tool_calls=(call,)),
            Msg(
                "tool",
                '<untrusted_data ref="E2" source="loki">x</untrusted_data>',
                tool_call_id="g1-0",
            ),
        ]
    )
    assert instruction == {"parts": [{"text": "rules"}]}
    assert contents[1] == {
        "role": "model",
        "parts": [{"functionCall": {"name": "query_logs", "args": {"service": "api"}}}],
    }
    response = contents[2]["parts"][0]["functionResponse"]
    assert response["name"] == "query_logs" and "untrusted_data" in response["response"]["content"]
    assert "untrusted_data" not in json.dumps(instruction)


@pytest.mark.parametrize("model", [HypothesisSetOut, DisproofPlanOut, ReflectionOut, ProposalOut])
def test_every_structured_schema_converts(model: Any) -> None:
    converted = json.dumps(to_gemini_schema(model.model_json_schema()))
    for forbidden in ('"$ref"', '"$defs"', '"title"', '"additionalProperties"', '"type": "null"'):
        assert forbidden not in converted


def test_nullable_and_free_form_objects() -> None:
    schema = to_gemini_schema(
        {
            "type": "object",
            "properties": {
                "a": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "p": {"type": "object"},
            },
        }
    )
    assert schema["properties"]["a"] == {"type": "string", "nullable": True}
    assert schema["properties"]["p"]["properties"]


async def test_embeddings_are_768_and_normalised() -> None:
    vector = [3.0] + [0.0] * 766 + [4.0]
    server = Server(
        httpx.Response(200, json={"embeddings": [{"values": vector}, {"values": vector}]})
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        embedder = GeminiEmbeddingProvider(client, KEY, sleep=server.sleep)
        out = await embedder.embed(["a", "b"], "RETRIEVAL_QUERY")
    assert len(out) == 2 and len(out[0]) == 768
    assert math.isclose(math.sqrt(sum(v * v for v in out[0])), 1.0)
    assert out[0][0] == pytest.approx(0.6)
    request = server.body()["requests"][0]
    assert request["taskType"] == "RETRIEVAL_QUERY" and request["outputDimensionality"] == 768
    assert str(server.requests[0].url).endswith("gemini-embedding-001:batchEmbedContents")


async def test_embeddings_with_the_wrong_size_are_refused() -> None:
    server = Server(httpx.Response(200, json={"embeddings": [{"values": [1.0] * 3072}]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(LLMUnavailableError, match="768"):
            await GeminiEmbeddingProvider(client, KEY, sleep=server.sleep).embed(
                ["a"], "RETRIEVAL_DOCUMENT"
            )
