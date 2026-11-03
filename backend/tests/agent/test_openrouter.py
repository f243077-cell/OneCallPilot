"""OpenRouter adapters against a fake HTTP server (ADR-23; CI never calls a real LLM)."""

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
from oncallpilot.agent.providers.openrouter import (
    NOT_RUN,
    OpenRouterEmbeddingProvider,
    OpenRouterProvider,
    to_messages,
)
from oncallpilot.agent.schemas import ProposalOut
from oncallpilot.agent.tools.registry import read_tool_specs

KEY = SecretStr("test-key-not-real")
SIGNATURE = [{"type": "reasoning.encrypted", "data": "opaque", "id": "call_1", "index": 0}]


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


def reply(message: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "choices": [{"message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        },
    )


def tool_call(call_id: str, name: str, arguments: Any) -> dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


async def provider(server: Server) -> tuple[OpenRouterProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return OpenRouterProvider(client, KEY, "google/test-flash", sleep=server.sleep), client


MESSAGES = [Msg("system", "rules"), Msg("user", "investigate E1")]


async def test_tool_turn_request_and_parsing() -> None:
    message = {
        "content": "checking",
        "tool_calls": [tool_call("call_1", "query_logs", '{"service": "api"}')],
        "reasoning_details": SIGNATURE,
    }
    server = Server(reply(message))
    llm, client = await provider(server)
    async with client:
        turn = await llm.step_with_tools(MESSAGES, read_tool_specs())
    request = server.requests[0]
    assert request.headers["authorization"] == "Bearer test-key-not-real"
    assert "test-key" not in str(request.url)
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    body = server.body()
    assert (body["model"], body["temperature"], body["tool_choice"]) == (
        "google/test-flash", 0, "auto",
    )  # fmt: skip
    assert body["reasoning"] == {"effort": "low"}
    assert body["provider"] == {"require_parameters": True, "data_collection": "deny"}
    assert body["messages"][0] == {"role": "system", "content": "rules"}
    declared = {t["function"]["name"] for t in body["tools"]}
    assert declared == {s.name for s in read_tool_specs()}
    assert turn.tool_calls == (
        ToolCall(id="call_1", name="query_logs", arguments={"service": "api"}),
    )
    assert (turn.done, turn.text, turn.usage.input_tokens) == (False, "checking", 120)
    assert turn.raw["reasoning_details"] == SIGNATURE


async def test_a_turn_without_calls_is_done() -> None:
    server = Server(reply({"content": "enough evidence"}))
    llm, client = await provider(server)
    async with client:
        turn = await llm.step_with_tools(MESSAGES, [])
    assert turn.done and not turn.tool_calls and "tools" not in server.body()


async def test_arguments_that_are_not_an_object_become_empty() -> None:
    calls = [tool_call("c1", "query_logs", "not json"), tool_call("c2", "query_logs", "[1]")]
    server = Server(reply({"content": "", "tool_calls": calls}))
    llm, client = await provider(server)
    async with client:
        turn = await llm.step_with_tools(MESSAGES, read_tool_specs())
    assert [c.arguments for c in turn.tool_calls] == [{}, {}]


def test_assistant_turns_are_replayed_unchanged_with_their_reasoning() -> None:
    raw = {
        "role": "assistant",
        "content": "",
        "tool_calls": [tool_call("call_1", "query_logs", '{"service":"api"}')],
        "reasoning_details": SIGNATURE,
    }
    out = to_messages(
        [
            *MESSAGES,
            Msg("assistant", "", raw=raw),
            Msg("tool", '<untrusted_data ref="E2">x</untrusted_data>', tool_call_id="call_1"),
        ]
    )
    assert out[2] == raw
    assert out[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '<untrusted_data ref="E2">x</untrusted_data>',
    }
    assert all("untrusted_data" not in m["content"] for m in out if m["role"] == "system")


def test_a_call_the_agent_did_not_run_gets_an_error_result() -> None:
    raw = {
        "role": "assistant",
        "content": "",
        "tool_calls": [tool_call("a", "query_logs", "{}"), tool_call("b", "query_metrics", "{}")],
    }
    out = to_messages(
        [
            *MESSAGES,
            Msg("assistant", "", raw=raw),
            Msg("tool", "ran", tool_call_id="a"),
            Msg("user", "now hypothesize"),
        ]  # fmt: skip
    )
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "tool", "user"]
    assert out[4] == {"role": "tool", "tool_call_id": "b", "content": NOT_RUN}


def test_an_assistant_turn_without_raw_is_rebuilt() -> None:
    call = ToolCall(id="c1", name="query_logs", arguments={"service": "api"})
    out = to_messages([Msg("assistant", "text", tool_calls=(call,)), Msg("tool", "r", "c1")])
    assert out[0]["tool_calls"][0]["function"] == {
        "name": "query_logs",
        "arguments": '{"service": "api"}',
    }


async def test_structured_output_uses_a_strict_schema_and_validates() -> None:
    good = {
        "action": "none",
        "params": {},
        "expected_effect": "x",
        "rationale": "y",
        "evidence_refs": ["E1"],
    }
    server = Server(
        reply({"content": json.dumps(good), "reasoning_details": SIGNATURE}),
        reply({"content": '{"action": "reboot"}'}),
    )
    llm, client = await provider(server)
    async with client:
        result = await llm.structured(MESSAGES, ProposalOut)
        assert result.value.action == "none" and result.raw["reasoning_details"] == SIGNATURE
        with pytest.raises(InvalidStructuredOutputError) as caught:
            await llm.structured(MESSAGES, ProposalOut)
    assert caught.value.usage.output_tokens == 30
    assert caught.value.text == '{"action": "reboot"}' and caught.value.raw is not None
    response_format = server.body()["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == ProposalOut.model_json_schema()
    assert server.body()["max_tokens"] == 8192


async def test_retry_once_on_429_then_succeed() -> None:
    server = Server(httpx.Response(429), reply({"content": "ok"}))
    llm, client = await provider(server)
    async with client:
        turn = await llm.step_with_tools(MESSAGES, [])
    assert turn.text == "ok" and server.sleeps == [2.0] and len(server.requests) == 2


@pytest.mark.parametrize("first,second", [(500, 503), (429, 429)])
async def test_two_failures_make_the_llm_unavailable(first: int, second: int) -> None:
    server = Server(httpx.Response(first), httpx.Response(second))
    llm, client = await provider(server)
    async with client:
        with pytest.raises(LLMUnavailableError):
            await llm.step_with_tools(MESSAGES, [])
    assert len(server.requests) == 2


@pytest.mark.parametrize("status", [400, 401, 402])
async def test_a_client_error_is_not_retried(status: int) -> None:
    server = Server(httpx.Response(status, json={"error": {"message": "no"}}))
    llm, client = await provider(server)
    async with client:
        with pytest.raises(LLMUnavailableError, match=str(status)):
            await llm.step_with_tools(MESSAGES, [])
    assert len(server.requests) == 1


async def test_a_refusal_reports_the_error_code_and_message_clipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = {"error": {"code": 400, "message": "Invalid schema " + "x" * 500,
                      "metadata": {"provider_name": "Google AI Studio"}}}  # fmt: skip
    server = Server(httpx.Response(400, json=body))
    llm, client = await provider(server)
    async with client:
        with pytest.raises(LLMUnavailableError, match="HTTP 400 400 Google AI Studio Invalid"):
            await llm.step_with_tools(MESSAGES, [])
    [record] = [r for r in caplog.records if r.name.endswith("openrouter")]
    assert len(record.getMessage()) < 300 and "test-key" not in record.getMessage()


async def test_an_error_body_with_status_200_is_retried() -> None:
    error = httpx.Response(200, json={"error": {"code": 502, "message": "upstream"}})
    server = Server(error, httpx.Response(200, content=b"not json"), reply({"content": "ok"}))
    llm, client = await provider(server)
    async with client:
        with pytest.raises(LLMUnavailableError, match="not JSON"):
            await llm.step_with_tools(MESSAGES, [])
        assert (await llm.step_with_tools(MESSAGES, [])).text == "ok"


async def test_network_errors_are_retried() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("slow")
        return reply({"content": "ok"})

    async def no_sleep(seconds: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(flaky)) as client:
        llm = OpenRouterProvider(client, KEY, "m", sleep=no_sleep)
        assert (await llm.step_with_tools(MESSAGES, [])).text == "ok"


def embeddings(*vectors: list[float]) -> httpx.Response:
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    return httpx.Response(200, json={"data": list(reversed(data))})


async def test_embeddings_are_768_normalised_and_in_input_order() -> None:
    first = [3.0] + [0.0] * 766 + [4.0]
    second = [0.0] * 767 + [2.0]
    server = Server(embeddings(first, second))
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        embedder = OpenRouterEmbeddingProvider(client, KEY, sleep=server.sleep)
        out = await embedder.embed(["a", "b"], "RETRIEVAL_QUERY")
    assert len(out) == 2 and len(out[0]) == 768
    assert math.isclose(math.sqrt(sum(v * v for v in out[0])), 1.0)
    assert out[0][0] == pytest.approx(0.6) and out[1][-1] == pytest.approx(1.0)
    body = server.body()
    assert (body["model"], body["dimensions"], body["input"]) == (
        "google/gemini-embedding-001", 768, ["a", "b"],
    )  # fmt: skip
    assert body["provider"] == {"data_collection": "deny"}
    assert str(server.requests[0].url) == "https://openrouter.ai/api/v1/embeddings"
    assert embedder.model == "openrouter/google/gemini-embedding-001"


async def test_embeddings_are_sent_in_batches_of_100() -> None:
    vector = [1.0] * 768
    server = Server(embeddings(*[vector] * 100), embeddings(vector))
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        out = await OpenRouterEmbeddingProvider(client, KEY).embed(
            ["t"] * 101, "RETRIEVAL_DOCUMENT"
        )
    assert len(out) == 101 and [len(server.body(i)["input"]) for i in (0, 1)] == [100, 1]


@pytest.mark.parametrize(
    "response,match",
    [
        (embeddings([1.0] * 3072), "768"),
        (embeddings([1.0] * 768, [1.0] * 768), "wrong number"),
    ],
)
async def test_bad_embeddings_are_refused(response: httpx.Response, match: str) -> None:
    server = Server(response)
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(LLMUnavailableError, match=match):
            await OpenRouterEmbeddingProvider(client, KEY, sleep=server.sleep).embed(
                ["a"], "RETRIEVAL_DOCUMENT"
            )
