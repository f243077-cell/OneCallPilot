"""HMAC-SHA256 signing of runner messages (C5 signing, SEC-003, RUN-001, RUN-015).

Implemented here from the C5 rules, and checked against the shared vector in
contracts/fixtures/signing/vectors.json by the tests:

- ``sig`` is the lower-case hex HMAC-SHA256 of the message without ``sig``,
  serialised with sorted keys, no whitespace and UTF-8 (``ensure_ascii=False``);
- the message is signed exactly as it travels, never re-serialised from a model;
- verification compares in constant time;
- ``execute`` verifies with ACTION_SIGNING_KEY only, ``dry_run`` and every
  result with RUNNER_LINK_KEY.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

SIG_FIELD = "sig"
MIN_KEY_BYTES = 32


class SigningKeyError(ValueError):
    """A signing key is missing, not hex, or shorter than 32 bytes."""


@dataclass(frozen=True, repr=False)
class Keys:
    """The runner's two keys. ``repr`` never shows them."""

    action: bytes  # ACTION_SIGNING_KEY: verifies execute
    link: bytes  # RUNNER_LINK_KEY: verifies dry_run, signs every result

    def for_request(self, message_type: str) -> bytes | None:
        """The only key that may verify a request of this type."""
        return {"execute": self.action, "dry_run": self.link}.get(message_type)

    def __repr__(self) -> str:
        return "Keys(<hidden>)"


def key_from_hex(name: str, value: str | None) -> bytes:
    if not value:
        raise SigningKeyError(f"{name} is not set")
    try:
        key = bytes.fromhex(value)
    except ValueError:
        raise SigningKeyError(f"{name} is not hex") from None
    if len(key) < MIN_KEY_BYTES:
        raise SigningKeyError(f"{name} must be at least {MIN_KEY_BYTES} bytes")
    return key


def canonical_json(message: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in message.items() if key != SIG_FIELD}
    text = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


def sign(message: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(key, canonical_json(message), hashlib.sha256).hexdigest()


def verify(message: Mapping[str, Any], key: bytes) -> bool:
    sig = message.get(SIG_FIELD)
    if not isinstance(sig, str):
        return False
    try:
        expected = sign(message, key)
    except ValueError:  # NaN or infinity cannot be canonical
        return False
    return hmac.compare_digest(expected, sig)
