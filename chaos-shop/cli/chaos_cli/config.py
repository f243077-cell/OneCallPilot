"""Fixed names and paths of the testbed, and the operator token."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RELEASES_FILE = REPO_ROOT / "chaos-shop" / "releases.yaml"
ENV_FILE = REPO_ROOT / ".env"

# compose.yaml `name:`; networks and volumes get this prefix.
PROJECT = "oncallpilot"
NETWORK = f"{PROJECT}_chaos_net"
# Shared deploy ledger (ADR-12): runner rw, backend-worker ro, chaos CLI via the helper.
LEDGER_VOLUME = f"{PROJECT}_deploy_ledger"
LEDGER_DIR = "/ledger"
LEDGER_FILE = f"{LEDGER_DIR}/deploys.jsonl"
LEDGER_UID = 10001

# Short-lived helper container on chaos_net (approved A1.3 networking option C).
HELPER_IMAGE = "python:3.12.15-slim"
HELPER_NAME_PREFIX = "ocp-ops-"

API_SLOTS = tuple(f"cs-api-{r}-{n}" for r in ("140", "150") for n in range(1, 6))
WORKER_SLOTS = ("cs-worker-210", "cs-worker-220")
SLOT_RELEASES = {"140": "1.4.0", "150": "1.5.0", "210": "2.1.0", "220": "2.2.0"}
SUPPORT = ("cs-postgres", "cs-redis", "cs-payments", "cs-lb", "cs-loadgen")
BASELINE_SLOTS = ("cs-api-140-1", "cs-worker-210")
BASELINE_RUNNING = (*SUPPORT, *BASELINE_SLOTS)
ALL_CONTAINERS = (*SUPPORT, *API_SLOTS, *WORKER_SLOTS)

LB_URL = "http://cs-lb"
PAYMENTS_URL = "http://cs-payments:8002"
LOADGEN_URL = "http://cs-loadgen:8003"
API_PORT = 8000
WORKER_PORT = 8001


def slot_release(name: str) -> str:
    """``cs-api-150-3`` -> ``1.5.0``; ``cs-worker-220`` -> ``2.2.0``."""
    return SLOT_RELEASES[name.split("-")[2]]


def api_slots(release: str) -> list[str]:
    return [name for name in API_SLOTS if slot_release(name) == release]


class ConfigError(Exception):
    """A setting the CLI needs is missing."""


@dataclass(frozen=True)
class Secrets:
    chaos_token: str


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_secrets(env_file: Path = ENV_FILE) -> Secrets:
    """``CHAOS_TOKEN`` from the environment, else from the git-ignored root .env."""
    token = os.environ.get("CHAOS_TOKEN") or read_env_file(env_file).get("CHAOS_TOKEN", "")
    if len(token) < 16:
        raise ConfigError("CHAOS_TOKEN is not set (environment or the root .env file)")
    return Secrets(chaos_token=token)
