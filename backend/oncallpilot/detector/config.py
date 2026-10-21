"""Detector config (``backend/config/detector.yaml``) and its version (architecture §6.8, ADR-20).

``detector_config_version`` is the SHA-256 of the file's bytes plus the
threshold overrides from ``monitor_settings``, so any change to either gives a
new version that is stamped on every incident opened afterwards.

Overrides use dotted paths into ``signals`` (``error_rate.z``); an unknown
path is refused, so a typo in Settings cannot silently do nothing.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PATH = Path("config") / "detector.yaml"


def config_version(config: bytes, overrides: dict[str, float] | None = None) -> str:
    digest = hashlib.sha256(config)
    digest.update(b"\0")
    digest.update(json.dumps(overrides or {}, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()


def read_config(path: Path = DEFAULT_PATH) -> bytes:
    return path.read_bytes()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StatisticalRule(_Strict):
    """EWMA z-score rule with absolute floors (error_rate, p95_latency, job_failure_rate)."""

    services: list[str] = Field(min_length=1)
    z: float = Field(gt=0)
    consecutive: int = Field(ge=1)
    min_value: float = Field(default=0.0, ge=0)
    min_factor: float = Field(default=0.0, ge=0, description="value >= factor x baseline")
    min_seconds: float = Field(default=0.0, ge=0)


class RestartsRule(_Strict):
    services: list[str] = Field(min_length=1)
    min_restarts: int = Field(ge=1)
    window_seconds: int = Field(ge=1)


class StackTracesRule(_Strict):
    services: list[str] = Field(min_length=1)
    min_per_minute: int = Field(ge=1)


class Signals(_Strict):
    error_rate: StatisticalRule
    p95_latency: StatisticalRule
    job_failure_rate: StatisticalRule
    restarts: RestartsRule
    stack_traces: StackTracesRule


class DetectorConfig(_Strict):
    ewma_alpha: float = Field(gt=0, lt=1)
    warmup_seconds: int = Field(ge=0)
    recovery_minutes: float = Field(gt=0)
    signals: Signals


def load_config(raw: bytes, overrides: dict[str, float] | None = None) -> DetectorConfig:
    """Parse the YAML and apply ``monitor_settings`` threshold overrides."""
    data: dict[str, Any] = yaml.safe_load(raw)
    for path, value in (overrides or {}).items():
        signal, _, field = path.partition(".")
        rule = data.get("signals", {}).get(signal)
        if not isinstance(rule, dict) or field not in rule or field == "services":
            raise ValueError(f"unknown detector setting {path!r}")
        rule[field] = value
    return DetectorConfig.model_validate(data)
