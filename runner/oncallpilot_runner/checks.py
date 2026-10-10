"""The runner's checks on one request, in C5's order (runner.py, §5.10 step 1).

    1. signature, with the one key for the message type   -> BAD_SIGNATURE
    2. message shape                                      -> VALIDATION_ERROR
    3. expires_at not yet passed                          -> REQUEST_EXPIRED
    4. catalogue_version equals the loaded catalogue's    -> VALIDATION_ERROR
    5. action enabled in the catalogue, with a handler    -> ACTION_NOT_ALLOWED
    6. parameters valid against the catalogue             -> VALIDATION_ERROR
    7. targets in runner/targets.yaml                     -> TARGET_NOT_ALLOWED
    8. execute only: SET NX ocp:runner:idem:{execution_id}; a duplicate is
       acknowledged and ignored with no result (RUN-005, brought forward)

**Every final answer to an execute claims its ID first** (C5): before a
refusal of an execute is sent, for checks 1-7 too, the runner sets
ocp:runner:idem:{execution_id} with SET NX. If the key already exists the
message is a duplicate and gets no result. So a forged message that borrows a
real execution_id ends that execution as refused, and the genuine execute
arriving later is a duplicate that never runs (C5 issue 9, closed).

**A message that cannot be answered gets no result** (C5): no usable type, no
UUID request_id, or an execute without a UUID execution_id is logged and
acknowledged.

Every failure fails closed: the request is refused (or dropped) and nothing
runs. Rate limit, cooldown and the state fingerprint (the rest of step 8) come
in tasks A2.5 and A2.3.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from oncallpilot_runner import request as shape
from oncallpilot_runner.catalogue import Catalogue, param_problems
from oncallpilot_runner.handlers import REGISTERED
from oncallpilot_runner.results import refused
from oncallpilot_runner.signing import Keys, verify
from oncallpilot_runner.targets import Targets

STREAM_FIELD = "msg"
IDEM_TTL_SECONDS = 24 * 3600


@dataclass(frozen=True)
class Accepted:
    request: shape.Request


@dataclass(frozen=True)
class Refused:
    error_code: str
    reason: str
    result: dict[str, Any]


@dataclass(frozen=True)
class Duplicate:
    execution_id: str


@dataclass(frozen=True)
class Dropped:
    """Too malformed to answer with a C5 result: logged and acknowledged."""

    reason: str


Decision = Accepted | Refused | Duplicate | Dropped

# key, ttl -> True if the key was set now (SET NX EX), False if it existed.
SetNx = Callable[[str, int], bool]
RuntimeValues = Callable[[str, dict[str, str | int]], set[str]]


@dataclass
class Checker:
    keys: Keys
    catalogue: Catalogue
    targets: Targets
    runtime_values: RuntimeValues
    set_nx: SetNx
    now: Callable[[], datetime]
    handlers: frozenset[str] = REGISTERED

    def check(self, fields: dict[str, str]) -> Decision:
        message = _message(fields)
        if isinstance(message, Dropped):
            return message
        target = _answerable(message)
        if isinstance(target, Dropped):
            return target
        kind = target.kind

        def refuse(code: str, reason: str) -> Refused | Duplicate:
            return self._refusal(target, code, reason)

        key = self.keys.for_request(kind)
        if key is None or not verify(message, key):  # 1
            return refuse("BAD_SIGNATURE", f"{kind} does not verify with its key")
        try:  # 2
            request = shape.parse(message)
        except shape.ShapeError as exc:
            return refuse("VALIDATION_ERROR", str(exc))
        if self.now() >= request.expires_at:  # 3
            return refuse("REQUEST_EXPIRED", f"expired at {request.expires_at.isoformat()}")
        if request.catalogue_version != self.catalogue.version:  # 4
            return refuse(
                "VALIDATION_ERROR",
                f"catalogue_version {request.catalogue_version} is not the loaded "
                f"{self.catalogue.version}",
            )
        action = self.catalogue.actions.get(request.action)  # 5
        if action is None or not action.enabled or request.action not in self.handlers:
            return refuse("ACTION_NOT_ALLOWED", f"{request.action} is not an enabled action")
        try:  # 6
            problems = param_problems(action, request.params, self.runtime_values)
        except ValueError as exc:  # the ledger cannot be read: fail closed
            return refuse("VALIDATION_ERROR", f"parameters cannot be checked: {exc}")
        if problems:
            return refuse("VALIDATION_ERROR", "; ".join(problems))
        for name, spec in action.params.items():  # 7
            if spec.live_check == "targets" and not self.targets.allows_service(
                str(request.params[name])
            ):
                return refuse("TARGET_NOT_ALLOWED", f"{request.params[name]} is not a target")
        if request.type == "execute" and request.execution_id:  # 8
            if not self._claim(request.execution_id):
                return Duplicate(request.execution_id)
        return Accepted(request)

    def refuse_unexpected(self, fields: dict[str, str]) -> Refused | Duplicate | Dropped:
        """A check raised something unexpected (a bug): refuse if the request can
        be answered at all, so it fails closed and the worker escalates."""
        message = _message(fields)
        if isinstance(message, Dropped):
            return message
        target = _answerable(message)
        if isinstance(target, Dropped):
            return target
        return self._refusal(target, "VALIDATION_ERROR", "internal error while checking")

    def _claim(self, execution_id: str) -> bool:
        return self.set_nx(f"ocp:runner:idem:{execution_id}", IDEM_TTL_SECONDS)

    def _refusal(self, target: "_Answer", code: str, reason: str) -> Refused | Duplicate:
        # An execute's final answer claims its ID first; a taken ID is a duplicate.
        if target.execution_id is not None and not self._claim(target.execution_id):
            return Duplicate(target.execution_id)
        result = refused(
            request_type=target.kind,
            request_id=target.request_id,
            execution_id=target.execution_id,
            error_code=code,
            now=self.now(),
            link_key=self.keys.link,
        )
        return Refused(code, reason, result)


@dataclass(frozen=True)
class _Answer:
    """What a refusal echoes: taken from the message as received (C5)."""

    kind: str
    request_id: str
    execution_id: str | None


def _answerable(message: dict[str, Any]) -> _Answer | Dropped:
    """Without a usable type, request_id (and execution_id for an execute) a
    refusal cannot be a valid C5 result, so the message is dropped instead."""
    kind = message.get("type")
    request_id = message.get("request_id")
    execution_id = message.get("execution_id")
    if kind not in ("dry_run", "execute") or not _uuid(request_id):
        return Dropped("no usable type or request_id")
    if kind == "execute" and not _uuid(execution_id):
        return Dropped("an execute without a usable execution_id")
    return _Answer(str(kind), str(request_id), str(execution_id) if kind == "execute" else None)


def _uuid(value: Any) -> bool:
    return isinstance(value, str) and shape.UUID.fullmatch(value) is not None


def _message(fields: dict[str, str]) -> dict[str, Any] | Dropped:
    """C5 transport: exactly one field, `msg`, holding the message as JSON."""
    if set(fields) != {STREAM_FIELD}:
        return Dropped(f"stream entry fields {sorted(fields)} are not exactly ['msg']")
    try:
        message = json.loads(fields[STREAM_FIELD])
    except ValueError:
        return Dropped("msg is not JSON")
    if not isinstance(message, dict):
        return Dropped("msg is not a JSON object")
    return message
