"""Entry point: ``python -m loadgen`` (cs-loadgen container, control port 8003)."""

import os

import httpx
from fastapi import FastAPI

from common.identity import ConfigError, Identity, require_env
from common.serve import serve
from loadgen.app import create_app
from loadgen.traffic import MAX_IN_FLIGHT, RateSchedule, TrafficGenerator

PORT = 8003


def _rate(name: str, default: str) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if not 0 <= value <= 1000:
        raise ConfigError(f"{name} must be between 0 and 1000, got {value}")
    return value


def build(identity: Identity) -> FastAPI:
    schedule = RateSchedule(
        baseline_rps=_rate("CS_LOADGEN_BASELINE_RPS", "5"),
        spike_rps=_rate("CS_LOADGEN_SPIKE_RPS", "40"),
        ramp_seconds=_rate("CS_LOADGEN_RAMP_SECONDS", "30"),
    )
    client = httpx.AsyncClient(
        base_url=require_env("CS_LOADGEN_TARGET_URL"),
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_connections=MAX_IN_FLIGHT, max_keepalive_connections=32),
    )
    generator = TrafficGenerator(client, schedule)
    return create_app(identity, generator, client, chaos_token=os.environ.get("CHAOS_TOKEN", ""))


def main() -> None:
    serve("loadgen", PORT, build, with_commit=False)


if __name__ == "__main__":
    main()
