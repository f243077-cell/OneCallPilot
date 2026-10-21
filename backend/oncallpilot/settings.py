"""Settings read from the environment (architecture §7.3, ``CLAUDE.md`` §10.2).

Each process receives only the variables it needs; secrets are ``SecretStr``
so they never appear in a repr or a log line.
"""

from __future__ import annotations

from pathlib import Path

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
    agent_enabled: bool = True
    detector_config_path: Path = Path("config") / "detector.yaml"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @property
    def jwks_url(self) -> str:
        return f"{self.jwt_issuer}/.well-known/jwks.json"
