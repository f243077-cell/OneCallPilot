"""Settings read from the environment (architecture §7.3, ``CLAUDE.md`` §10.2).

Each process receives only the variables it needs; secrets are ``SecretStr``
so they never appear in a repr or a log line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """backend-api settings."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    database_url: SecretStr
    redis_url: SecretStr
    supabase_url: str
    ingest_token: SecretStr
    action_signing_key: SecretStr  # hex; signs execute requests (SEC-002)
    fcm_credentials_path: Path | None = None  # mounted service-account file (FR-018)
    agent_enabled: bool = True
    detector_config_path: Path = Path("config") / "detector.yaml"
    benchmark_lock: bool = False  # true after bench-freeze: settings are read-only (ADR-20)
    loki_url: str = "http://loki:3100"  # /log-tail

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.jwt_issuer}/.well-known/jwks.json"


class WorkerSettings(BaseSettings):
    """backend-worker settings. It never receives ``ACTION_SIGNING_KEY`` (SEC-002)."""

    model_config = SettingsConfigDict(extra="ignore", frozen=True)

    database_url: SecretStr
    redis_url: SecretStr
    runner_link_key: SecretStr  # hex; signs dry_run requests, verifies runner results
    ingest_token: SecretStr
    ingest_url: str = "http://backend-api:8000/ingest/alert"
    llm_provider: Literal["gemini", "anthropic", "fake"] = "gemini"
    llm_model: str = "gemini-2.5-flash"
    gemini_api_key: SecretStr | None = None  # LLM and embeddings (ADR-14)
    anthropic_api_key: SecretStr | None = None  # Claude adapter (ADR-13 fallback)
    prompt_version: str = "v1"
    confidence_threshold: float = 0.70
    max_agent_seconds: float = 90.0
    agent_concurrency: int = 2
    proposal_ttl_seconds: int = 900
    execution_timeout_seconds: int = 240
    verify_window_seconds: int = 60
    verify_timeout_seconds: int = 300
    detector_interval_seconds: float = 5.0
    detector_config_path: Path = Path("config") / "detector.yaml"
    prometheus_url: str = "http://prometheus:9090"
    loki_url: str = "http://loki:3100"
    docker_host: str = "tcp://socket-proxy-ro:2375"
    ledger_path: Path = Path("/ledger/deploys.jsonl")
    catalogue_path: Path = Path("catalogue") / "actions.yaml"
    targets_path: Path = Path("catalogue") / "targets.yaml"
    runbooks_dir: Path = Path("runbooks")
    fcm_credentials_path: Path | None = None  # mounted service-account file (FR-018)
