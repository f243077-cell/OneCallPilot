"""Signed runner results for tests, with the TEST-ONLY keys of
``contracts/fixtures/signing/vectors.json``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oncallpilot_contracts.signing import key_from_hex, sign

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "signing" / "vectors.json"
    ).read_text("utf-8")
)
LINK_KEY = key_from_hex(VECTORS["keys"]["RUNNER_LINK_KEY"])
ACTION_KEY = key_from_hex(VECTORS["keys"]["ACTION_SIGNING_KEY"])
STATE_FINGERPRINT = "b94ac02137f28bc69ac0d32c382537a31d9477b6dde2dedc27c1a734c3fcaf42"
DRY_RUN_AT = datetime(2026, 10, 21, 10, 4, 58, tzinfo=UTC)


def dry_run(active: str | None = "1.5.0", replicas: int | None = 1) -> dict[str, Any]:
    return {
        "would_change": [
            {"target": "cs-api-140-1", "change": "start"},
            {"target": "cs-api-150-1", "change": "stop"},
        ],
        "current": {
            "active_release": active,
            "running_replicas": replicas,
            "containers": [{"name": "cs-api-150-1", "state": "running", "health": "healthy"}],
            "cache_keys": None,
        },
        "preconditions": {"rate_limit_ok": True, "cooldown_ok": True, "slots_available": True},
        "predicted_effect": "1 replica of api 1.4.0 serves traffic; 1.5.0 is stopped",
        "warnings": [],
        "state_fingerprint": STATE_FINGERPRINT,
    }


def dry_run_reply(
    request_id: str,
    *,
    error_code: str | None = None,
    key: bytes = LINK_KEY,
    ts: datetime = DRY_RUN_AT,
    **current: Any,
) -> dict[str, Any]:
    """A signed ``dry_run_result``: ``ok`` unless ``error_code`` is given."""
    reply: dict[str, Any] = {
        "type": "dry_run_result",
        "request_id": request_id,
        "execution_id": None,
        "status": "refused" if error_code else "ok",
        "error_code": error_code,
        "dry_run": None if error_code else dry_run(**current),
        "step": None,
        "health_after": None,
        "captured": None,
        "ts": ts.isoformat().replace("+00:00", "Z"),
    }
    reply["sig"] = sign(reply, key)
    return reply


def structural(passed: bool = True, at: datetime = DRY_RUN_AT) -> dict[str, Any]:
    return {
        "passed": passed,
        "checked_at": at.isoformat().replace("+00:00", "Z"),
        "containers": [{"name": "cs-api-140-1", "state": "running", "health": "healthy"}],
        "probes": [
            {
                "url": "http://cs-lb/healthz",
                "ok": passed,
                "status_code": 200 if passed else 502,
                "latency_ms": 3.0,
                "error": None,
            }
        ],
    }


def execution_reply(
    execution_id: str,
    status: str,
    *,
    error_code: str | None = None,
    captured: dict[str, Any] | None = None,
    health: bool | None = None,
    step: str | None = None,
    key: bytes = LINK_KEY,
    ts: datetime = DRY_RUN_AT,
) -> dict[str, Any]:
    """A signed ``execution_result`` (or ``progress`` when ``step`` is given)."""
    reply: dict[str, Any] = {
        "type": "progress" if step else "execution_result",
        "request_id": "3b9d6a1e-5f2c-4e8a-9d71-0c4b8e2f6a13",
        "execution_id": execution_id,
        "status": "ok" if step else status,
        "error_code": error_code,
        "dry_run": None,
        "step": {"name": step, "status": status, "message": "working"} if step else None,
        "health_after": None if health is None else {"structural": structural(health, ts)},
        "captured": captured,
        "ts": ts.isoformat().replace("+00:00", "Z"),
    }
    reply["sig"] = sign(reply, key)
    return reply
