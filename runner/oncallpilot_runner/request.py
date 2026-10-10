"""Shape of a runner request (C5 ``RunnerRequest``, architecture §9.5).

The rules of the C5 model, checked with the standard library: every field
present and no other (extra fields are refused), the types and patterns, the
approval fields on ``execute`` (SEC-002), and ``expires_at`` after
``issued_at``. ``action`` and ``params`` are only shape-checked here; the
catalogue decides what they may be (RUN-003, RUN-018).
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ACTION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PARAM_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SIGNATURE = re.compile(r"^[0-9a-f]{64}$")

FIELDS = (
    "type", "request_id", "execution_id", "proposal_id", "action", "params",
    "state_fingerprint", "idempotency_key", "approved_by", "approved_at",
    "issued_at", "expires_at", "catalogue_version", "sig",
)  # fmt: skip
APPROVAL_FIELDS = ("execution_id", "idempotency_key", "approved_by", "approved_at")


class ShapeError(ValueError):
    """The message does not have the C5 request shape (VALIDATION_ERROR)."""


@dataclass(frozen=True)
class Request:
    type: str
    request_id: str
    execution_id: str | None
    proposal_id: str | None
    action: str
    params: dict[str, str | int]
    state_fingerprint: str | None
    idempotency_key: str | None
    approved_by: str | None
    approved_at: datetime | None
    issued_at: datetime
    expires_at: datetime
    catalogue_version: str


def utc(value: Any, field: str) -> datetime:
    """An RFC 3339 timestamp in UTC (C5 UtcDatetime: timezone-aware, UTC only)."""
    if not isinstance(value, str):
        raise ShapeError(f"{field} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ShapeError(f"{field} is not an ISO 8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ShapeError(f"{field} must be in UTC")
    return parsed.astimezone(UTC)


def _match(value: Any, pattern: re.Pattern[str], field: str, *, optional: bool = False) -> Any:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ShapeError(f"{field} is missing or malformed")
    return value


def parse(message: dict[str, Any]) -> Request:
    missing = [f for f in FIELDS if f not in message]
    extra = sorted(set(message) - set(FIELDS))
    if missing or extra:
        raise ShapeError(f"fields missing {missing} or not allowed {extra}")
    kind = message["type"]
    if kind not in ("dry_run", "execute"):
        raise ShapeError("type must be dry_run or execute")
    params = message["params"]
    if not isinstance(params, dict):
        raise ShapeError("params must be an object")
    for name, value in params.items():
        if not PARAM_NAME.fullmatch(name):
            raise ShapeError(f"parameter name {name!r} is malformed")
        # StrictStr | StrictInt: a bool is not an int here.
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ShapeError(f"parameter {name} must be a string or an integer")
    request = Request(
        type=kind,
        request_id=_match(message["request_id"], UUID, "request_id"),
        execution_id=_match(message["execution_id"], UUID, "execution_id", optional=True),
        proposal_id=_match(message["proposal_id"], UUID, "proposal_id", optional=True),
        action=_match(message["action"], ACTION, "action"),
        params=dict(params),
        state_fingerprint=_match(
            message["state_fingerprint"], SHA256, "state_fingerprint", optional=True
        ),
        idempotency_key=_match(message["idempotency_key"], UUID, "idempotency_key", optional=True),
        approved_by=_match(message["approved_by"], UUID, "approved_by", optional=True),
        approved_at=None
        if message["approved_at"] is None
        else utc(message["approved_at"], "approved_at"),
        issued_at=utc(message["issued_at"], "issued_at"),
        expires_at=utc(message["expires_at"], "expires_at"),
        catalogue_version=_match(message["catalogue_version"], SEMVER, "catalogue_version"),
    )
    _match(message["sig"], SIGNATURE, "sig")
    if kind == "execute":
        absent = [
            f for f in (*APPROVAL_FIELDS, "proposal_id", "state_fingerprint")
            if message[f] is None
        ]  # fmt: skip
        if absent:
            raise ShapeError(f"execute needs {', '.join(absent)}")
    else:
        present = [f for f in APPROVAL_FIELDS if message[f] is not None]
        if present:
            raise ShapeError(f"dry_run must not carry {', '.join(present)}")
    if request.expires_at <= request.issued_at:
        raise ShapeError("expires_at must be after issued_at")
    return request
