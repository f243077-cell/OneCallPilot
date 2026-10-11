"""Results on ocp:runner:results (C5 RunnerResult, architecture §9.5, RUN-015).

Every result is signed with RUNNER_LINK_KEY. A refusal answers a request with
`status: refused` and its `error_code`; the worker then fails the execution
and escalates the incident as `runner_refused` (§5.10 step 6).
"""

from datetime import UTC, datetime
from typing import Any

from oncallpilot_runner.signing import sign


def timestamp(now: datetime) -> str:
    """C5 UtcDatetime on the wire: ISO 8601 with a Z suffix."""
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def refused(
    *,
    request_type: str,
    request_id: str,
    execution_id: str | None,
    error_code: str,
    now: datetime,
    link_key: bytes,
) -> dict[str, Any]:
    """The refused result for a request: `dry_run_result` for a dry run,
    `execution_result` for an execute (C5)."""
    result: dict[str, Any] = {
        "type": "dry_run_result" if request_type == "dry_run" else "execution_result",
        "request_id": request_id,
        "execution_id": None if request_type == "dry_run" else execution_id,
        "status": "refused",
        "error_code": error_code,
        "dry_run": None,
        "step": None,
        "health_after": None,
        "captured": None,
        "ts": timestamp(now),
    }
    result["sig"] = sign(result, link_key)
    return result


def dry_run_ok(
    *, request_id: str, dry_run: dict[str, Any], now: datetime, link_key: bytes
) -> dict[str, Any]:
    """The answer to a dry run that read the state: `ok` with C1's `DryRunResult`."""
    result: dict[str, Any] = {
        "type": "dry_run_result",
        "request_id": request_id,
        "execution_id": None,
        "status": "ok",
        "error_code": None,
        "dry_run": dry_run,
        "step": None,
        "health_after": None,
        "captured": None,
        "ts": timestamp(now),
    }
    result["sig"] = sign(result, link_key)
    return result


def progress(
    *, request_id: str, execution_id: str, name: str, status: str, message: str, now: datetime,
    link_key: bytes,
) -> dict[str, Any]:  # fmt: skip
    """One execution step (RUN-013): `progress`, status ok, with `step`."""
    result: dict[str, Any] = {
        "type": "progress",
        "request_id": request_id,
        "execution_id": execution_id,
        "status": "ok",
        "error_code": None,
        "dry_run": None,
        "step": {"name": name[:100], "status": status, "message": message[:300]},
        "health_after": None,
        "captured": None,
        "ts": timestamp(now),
    }
    result["sig"] = sign(result, link_key)
    return result


def execution_result(
    *, request_id: str, execution_id: str, status: str, error_code: str | None,
    structural: dict[str, Any] | None, captured: dict[str, Any] | None, now: datetime,
    link_key: bytes,
) -> dict[str, Any]:  # fmt: skip
    """The final answer to an execute (C5): succeeded and failed carry
    `health_after` (when the check ran) and `captured`; aborted carries only
    `error_code`."""
    final = status in ("succeeded", "failed")
    result: dict[str, Any] = {
        "type": "execution_result",
        "request_id": request_id,
        "execution_id": execution_id,
        "status": status,
        "error_code": error_code,
        "dry_run": None,
        "step": None,
        "health_after": {"structural": structural} if final and structural else None,
        "captured": captured if final else None,
        "ts": timestamp(now),
    }
    result["sig"] = sign(result, link_key)
    return result
