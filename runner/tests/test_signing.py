"""Signing (SEC-003): the runner's implementation against the shared C5 vector.

The vector lives in contracts/fixtures/signing/vectors.json. Until the contracts
are merged into this branch, point OCP_CONTRACTS_DIR at a checkout that has it;
without it the vector tests are skipped and the rest still run.
"""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from oncallpilot_runner.checks import Accepted, Checker
from oncallpilot_runner.request import utc
from oncallpilot_runner.signing import (
    Keys,
    SigningKeyError,
    canonical_json,
    key_from_hex,
    sign,
    verify,
)
from tests.publish_signed import entry, vector_keys, vector_path

VECTOR = vector_path()
needs_vector = pytest.mark.skipif(
    VECTOR is None, reason="signing vector not found (set OCP_CONTRACTS_DIR)"
)


def vectors() -> list[dict[str, Any]]:
    assert VECTOR is not None
    data: list[dict[str, Any]] = json.loads(VECTOR.read_text(encoding="utf-8"))["vectors"]
    return data


@needs_vector
def test_every_vector_verifies_exactly_as_labelled() -> None:
    assert VECTOR is not None
    keys = vector_keys(VECTOR)
    by_name = {"ACTION_SIGNING_KEY": keys.action, "RUNNER_LINK_KEY": keys.link}
    for vector in vectors():
        message = vector["message"]
        assert canonical_json(message).decode("utf-8") == vector["canonical"], vector["name"]
        assert verify(message, by_name[vector["verify_with"]]) is vector["valid"], vector["name"]


@needs_vector
def test_the_runner_accepts_and_refuses_the_vector_requests(
    checker: Checker, ledger: Path, clock: list[Any]
) -> None:
    assert VECTOR is not None
    checker.keys = vector_keys(VECTOR)
    for vector in vectors():
        message = vector["message"]
        if message["type"] not in ("execute", "dry_run"):
            continue
        clock[0] = utc(message["issued_at"], "issued_at") + timedelta(seconds=1)
        decision = checker.check(entry(message))
        if vector["valid"]:
            assert isinstance(decision, Accepted), vector["name"]
        else:
            assert getattr(decision, "error_code", None) == "BAD_SIGNATURE", vector["name"]


@needs_vector
def test_our_results_verify_like_the_vector_results() -> None:
    assert VECTOR is not None
    link = vector_keys(VECTOR).link
    for vector in vectors():
        if vector["message"]["type"] in ("dry_run_result", "progress", "execution_result"):
            assert sign(vector["message"], link) == vector["message"]["sig"], vector["name"]


def test_canonical_json_is_sorted_compact_utf8_and_ignores_sig() -> None:
    message = {"b": 1, "a": "…", "sig": "x"}
    assert canonical_json(message) == '{"a":"…","b":1}'.encode()


def test_verification_fails_closed_on_odd_input() -> None:
    key = b"k" * 32
    assert not verify({"a": 1}, key)  # no sig
    assert not verify({"a": 1, "sig": 5}, key)  # sig not a string
    assert not verify({"a": float("nan"), "sig": "0" * 64}, key)  # cannot be canonical


def test_keys_are_hex_32_bytes_and_never_shown() -> None:
    assert key_from_hex("K", "ab" * 32) == bytes.fromhex("ab" * 32)
    for bad in (None, "", "zz" * 32, "ab" * 31):
        with pytest.raises(SigningKeyError):
            key_from_hex("K", bad)
    keys = Keys(action=b"a" * 32, link=b"b" * 32)
    assert "a" * 8 not in repr(keys) and repr(keys) == "Keys(<hidden>)"
    assert keys.for_request("execute") == b"a" * 32 and keys.for_request("dry_run") == b"b" * 32
    assert keys.for_request("execution_result") is None
