"""backend-worker wiring: provider choice and the run metadata (architecture §6.7; SEC-002)."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from oncallpilot.agent.providers.fake import FakeProvider
from oncallpilot.agent.providers.gemini import GeminiProvider
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
