"""Claude adapter against a mocked Messages API (AI-021; ADR-13). CI never calls Claude."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest

from oncallpilot.agent.providers.anthropic import NOT_RUN, AnthropicProvider, to_messages
from oncallpilot.agent.providers.base import (
    InvalidStructuredOutputError,
    LLMUnavailableError,
    Msg,
    ToolCall,
    ToolSpec,
)
from oncallpilot.agent.schemas import ProposalOut

THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
PROPOSAL = {
    "action": "rollback_deploy",
    "params": {"service": "api", "target_release": "1.4.0"},
    "expected_effect": "errors stop",
    "rationale": "deploy then errors",
    "evidence_refs": ["E2"],
}


def reply(content: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


class Api:
    def __init__(self, *answers: httpx2.Response | dict[str, Any]) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[Any] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        self.headers.append(request.headers)
        answer = self.answers.pop(0)
        return answer if isinstance(answer, httpx2.Response) else httpx2.Response(200, json=answer)

    def provider(self) -> AnthropicProvider:
        client = anthropic.AsyncAnthropic(
            api_key="test-key-not-real",
            base_url="http://claude.test",
            max_retries=1,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self.handler)),
        )
        return AnthropicProvider(client, "claude-opus-5-5")


TOOLS = [ToolSpec("query_logs", "Read logs", {"type": "object", "properties": {}})]


async def test_gather_turn_returns_tool_calls_and_the_raw_content() -> None:
    api = Api(
        reply(
            [THINKING, {"type": "text", "text": "Checking logs."},
             {"type": "tool_use", "id": "toolu_1", "name": "query_logs",
              "input": {"service": "api"}}],
            stop="tool_use",
        )
    )  # fmt: skip
    turn = await api.provider().step_with_tools(
        [Msg("system", "you are careful"), Msg("user", "investigate")], TOOLS
    )
    assert turn.tool_calls == (ToolCall("toolu_1", "query_logs", {"service": "api"}),)
    assert not turn.done and turn.text == "Checking logs."
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (120, 30)
    assert turn.raw[0]["signature"] == "sig-abc"
    body = api.bodies[0]
    assert body["model"] == "claude-opus-5-5" and body["system"] == "you are careful"
    assert body["tool_choice"] == {"type": "auto"}
    assert body["tools"][0]["input_schema"] == {"type": "object", "properties": {}}
    assert body["output_config"] == {"effort": "low"}
    assert "temperature" not in body and "thinking" not in body
    # The key travels only in a request header (the SDK chooses which), never in the body.
    headers = api.headers[0]
    assert any("test-key-not-real" in headers.get(h, "") for h in ("authorization", "x-api-key"))
    assert "test-key-not-real" not in json.dumps(body)


async def test_structured_reply_is_validated_and_replayed_unchanged() -> None:
    fenced = "```json\n" + json.dumps(PROPOSAL) + "\n```"
    api = Api(reply([THINKING, {"type": "text", "text": fenced}]), reply([THINKING]))
    provider = api.provider()
    history = [Msg("user", "investigate"), Msg("user", "propose")]
    result = await provider.structured(history, ProposalOut)
    assert result.value.params == {"service": "api", "target_release": "1.4.0"}
    prompt = api.bodies[0]["messages"][-1]["content"][-1]["text"]
    assert prompt.startswith("propose\n\nReply with only one JSON object")

    # The next request carries that prompt and the reply exactly as they were.
    later = [*history, Msg("assistant", "ignored", raw=result.raw), Msg("user", "next")]
    await provider.step_with_tools(later, TOOLS)
    sent = api.bodies[1]["messages"]
    assert sent[0]["content"][-1]["text"] == prompt
    assert sent[1] == {"role": "assistant", "content": result.raw}


async def test_invalid_and_refused_structured_replies() -> None:
    api = Api(
        reply([{"type": "text", "text": '{"action": "reboot"}'}]),
        reply([], stop="refusal"),
    )
    provider = api.provider()
    with pytest.raises(InvalidStructuredOutputError) as invalid:
        await provider.structured([Msg("user", "propose")], ProposalOut)
    assert invalid.value.raw == [{"type": "text", "text": '{"action": "reboot"}'}]
    assert invalid.value.text == '{"action": "reboot"}'
    with pytest.raises(InvalidStructuredOutputError, match="declined"):
        await provider.structured([Msg("user", "propose")], ProposalOut)


@pytest.mark.parametrize(
    ("answers", "calls"),
    [
        ([httpx2.Response(429, json={"type": "error"}), httpx2.Response(529, json={})], 2),
        (
            [
                httpx2.Response(
                    400,
                    json={
                        "type": "error",
                        "error": {"type": "invalid_request_error", "message": "bad"},
                    },
                )
            ],
            1,
        ),
    ],
)
async def test_api_errors_become_llm_unavailable(
    answers: list[httpx2.Response], calls: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = Api(*answers)
    provider = api.provider()
    monkeypatch.setattr(provider._client, "_calculate_retry_timeout", lambda *a, **k: 0.0)
    with pytest.raises(LLMUnavailableError):
        await provider.step_with_tools([Msg("user", "go")], TOOLS)
    assert len(api.bodies) == calls  # one retry for 429/5xx, none for 400


def test_unrun_tool_calls_get_an_error_result_and_results_come_first() -> None:
    raw = [
        THINKING,
        {"type": "tool_use", "id": "a", "name": "query_logs", "input": {}},
        {"type": "tool_use", "id": "b", "name": "query_logs", "input": {}},
    ]
    _, turns = to_messages(
        [
            Msg("user", "go"),
            Msg("assistant", "", raw=raw),
            Msg("tool", "lines", tool_call_id="a"),
            Msg("user", "hypothesize"),
        ]
    )
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    last: Callable[[int], dict[str, Any]] = lambda i: turns[2]["content"][i]  # noqa: E731
    assert (last(0)["tool_use_id"], last(0)["content"]) == ("a", "lines")
    assert (last(1)["tool_use_id"], last(1)["is_error"], last(1)["content"]) == ("b", True, NOT_RUN)
    assert last(2) == {"type": "text", "text": "hypothesize"}


def test_messages_without_raw_content_are_rebuilt() -> None:
    _, turns = to_messages(
        [Msg("user", "go"),
         Msg("assistant", "", tool_calls=(ToolCall("c1", "query_logs", {"service": "api"}),)),
         Msg("tool", "lines", tool_call_id="c1")]
    )  # fmt: skip
    assert turns[1]["content"] == [
        {"type": "tool_use", "id": "c1", "name": "query_logs", "input": {"service": "api"}}
    ]
    assert turns[2]["content"][0]["type"] == "tool_result"
