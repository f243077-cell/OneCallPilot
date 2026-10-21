"""backend-worker wiring: provider choice and the run metadata (architecture §6.7; SEC-002)."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from oncallpilot.agent.providers.base import LLMUnavailableError, Msg
from oncallpilot.agent.providers.fake import FakeProvider
from oncallpilot.agent.providers.gemini import GeminiProvider
from oncallpilot.agent.schemas import HypothesisSetOut
from oncallpilot.settings import WorkerSettings
from oncallpilot.worker.__main__ import build_providers, run_meta

BASE = {
    "database_url": SecretStr("postgresql://x"),
    "redis_url": SecretStr("redis://x"),
    "runner_link_key": SecretStr("00" * 32),
    "ingest_token": SecretStr("t"),
}


def settings(**overrides: object) -> WorkerSettings:
    return WorkerSettings.model_validate(BASE | overrides)


async def test_fake_provider_needs_no_key() -> None:
    async with httpx.AsyncClient() as http:
        provider, _ = build_providers(settings(llm_provider="fake"), http)
    assert isinstance(provider, FakeProvider)


async def test_the_development_fake_behaves_like_an_unavailable_llm() -> None:
    """Nothing is scripted in a running worker: every structured call fails as
    an unavailable LLM, so each investigation escalates `llm_unavailable`
    instead of crashing the job (found on the first real start)."""
    async with httpx.AsyncClient() as http:
        provider, _ = build_providers(settings(llm_provider="fake"), http)
    with pytest.raises(LLMUnavailableError):
        await provider.structured([Msg("user", "hypothesize")], HypothesisSetOut)


async def test_gemini_needs_its_key() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(SystemExit):
            build_providers(settings(llm_provider="gemini"), http)
        provider, _ = build_providers(
            settings(llm_provider="gemini", gemini_api_key=SecretStr("k"), llm_model="m-1"), http
        )
    assert isinstance(provider, GeminiProvider) and provider.model == "m-1"


def test_run_meta_hash_changes_with_agent_settings() -> None:
    fake = FakeProvider()
    base = run_meta(settings(), fake, "1.0.0")
    assert set(base) == {
        "prompt_version",
        "provider",
        "model",
        "catalogue_version",
        "agent_config_hash",
    }
    assert run_meta(settings(), fake, "1.0.0") == base
    for changed in (
        run_meta(settings(confidence_threshold=0.8), fake, "1.0.0"),
        run_meta(settings(prompt_version="v2"), fake, "1.0.0"),
        run_meta(settings(), fake, "1.1.0"),
    ):
        assert changed["agent_config_hash"] != base["agent_config_hash"]


def test_worker_settings_have_no_action_signing_key() -> None:
    assert "action_signing_key" not in WorkerSettings.model_fields


async def test_anthropic_provider_needs_its_key_and_keeps_gemini_embeddings() -> None:
    from oncallpilot.agent.providers.anthropic import AnthropicProvider
    from oncallpilot.agent.providers.gemini import GeminiEmbeddingProvider

    async with httpx.AsyncClient() as http:
        with pytest.raises(SystemExit):
            build_providers(settings(llm_provider="anthropic"), http)
        provider, embedder = build_providers(
            settings(
                llm_provider="anthropic",
                llm_model="claude-opus-5-5",
                anthropic_api_key=SecretStr("k"),
                gemini_api_key=SecretStr("g"),
            ),
            http,
        )
    assert isinstance(provider, AnthropicProvider) and provider.model == "claude-opus-5-5"
    assert isinstance(embedder, GeminiEmbeddingProvider)
