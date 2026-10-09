"""C5 — Signing of runner messages (architecture §9.5, ADR-10; SEC-002, SEC-003, RUN-001, RUN-015).

Algorithm
    ``sig`` is the lower-case hex HMAC-SHA256 of ``canonical_json(message)``:
    the message **without** its ``sig`` field, serialised with sorted keys,
    no whitespace, and UTF-8 (``json.dumps(..., sort_keys=True,
    separators=(",", ":"), ensure_ascii=False)``). Sign and verify the JSON
    object exactly as it travels on the stream, never a re-serialised model,
    so both sides hash the same bytes. Verification compares in constant
    time (``hmac.compare_digest``).

Keys
    Two keys, each at least 32 random bytes, stored **hex-encoded** in env
    vars (``key_from_hex`` decodes and checks them):

    ======================== ================================ =============================
    key                      signs                            held by
    ======================== ================================ =============================
    ``ACTION_SIGNING_KEY``   ``execute`` requests             backend-api, runner
    ``RUNNER_LINK_KEY``      ``dry_run`` requests and every   backend-api, backend-worker,
                             runner result                    runner
    ======================== ================================ =============================

    backend-worker never holds ``ACTION_SIGNING_KEY``, so it cannot produce
    an ``execute`` that the runner accepts. The runner verifies an
    ``execute`` with ``ACTION_SIGNING_KEY`` only; one signed with
    ``RUNNER_LINK_KEY`` is refused with ``BAD_SIGNATURE``.

Test vectors for both keys: ``contracts/fixtures/signing/vectors.json``
(SEC-003). Their keys are test-only and must never be used anywhere else.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any, Literal

SIG_FIELD = "sig"
MIN_KEY_BYTES = 32

KeyName = Literal["ACTION_SIGNING_KEY", "RUNNER_LINK_KEY"]

_KEY_FOR_TYPE: dict[str, KeyName] = {
    "execute": "ACTION_SIGNING_KEY",
    "dry_run": "RUNNER_LINK_KEY",
    "dry_run_result": "RUNNER_LINK_KEY",
    "progress": "RUNNER_LINK_KEY",
    "execution_result": "RUNNER_LINK_KEY",
}


def canonical_json(message: Mapping[str, Any]) -> bytes:
    """The bytes that are signed: the message without ``sig``, canonically serialised."""
    body = {key: value for key, value in message.items() if key != SIG_FIELD}
    text = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


def key_from_hex(value: str) -> bytes:
    """Decode a key from its env-var form (hex); refuse keys shorter than 32 bytes."""
    key = bytes.fromhex(value)
    if len(key) < MIN_KEY_BYTES:
        raise ValueError(f"signing keys must be at least {MIN_KEY_BYTES} bytes")
    return key


def key_name_for(message_type: str) -> KeyName:
    """The key that signs (and verifies) a message of this ``type``."""
    try:
        return _KEY_FOR_TYPE[message_type]
    except KeyError:
        raise ValueError(f"unknown runner message type {message_type!r}") from None


def sign(message: Mapping[str, Any], key: bytes) -> str:
    """Return ``sig`` for ``message`` (any existing ``sig`` field is ignored)."""
    return hmac.new(key, canonical_json(message), hashlib.sha256).hexdigest()


def verify(message: Mapping[str, Any], key: bytes) -> bool:
    """True only if ``message['sig']`` is the signature of the rest of the message."""
    sig = message.get(SIG_FIELD)
    if not isinstance(sig, str):
        return False
    return hmac.compare_digest(sign(message, key), sig)
