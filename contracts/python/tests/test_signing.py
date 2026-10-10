"""C5 signing: the shared test vectors (SEC-003) and the helper functions."""

import json
from pathlib import Path
from typing import Any

import pytest

from oncallpilot_contracts.runner import RunnerRequest, RunnerResult
from oncallpilot_contracts.signing import (
    canonical_json,
    key_from_hex,
    key_name_for,
    sign,
    verify,
)

VECTORS_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "signing" / "vectors.json"
DOC: dict[str, Any] = json.loads(VECTORS_FILE.read_text("utf-8"))
KEYS = {name: key_from_hex(value) for name, value in DOC["keys"].items()}
VECTORS: list[dict[str, Any]] = DOC["vectors"]


def by_name(name: str) -> dict[str, Any]:
    return next(vector for vector in VECTORS if vector["name"] == name)


def test_test_keys_are_labelled_and_distinct() -> None:
    assert set(KEYS) == {"ACTION_SIGNING_KEY", "RUNNER_LINK_KEY"}
    for key in KEYS.values():
        assert len(key) >= 32
        assert key.startswith(b"TEST ONLY")
    assert KEYS["ACTION_SIGNING_KEY"] != KEYS["RUNNER_LINK_KEY"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(vector: dict[str, Any]) -> None:
    message = vector["message"]
    key = KEYS[vector["verify_with"]]
    assert canonical_json(message) == vector["canonical"].encode("utf-8")
    assert verify(message, key) is vector["valid"]
    if vector["valid"]:
        assert sign(message, key) == message["sig"]
        assert key_name_for(message["type"]) == vector["verify_with"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector_messages_are_valid_shapes(vector: dict[str, Any]) -> None:
    message = vector["message"]
    model = RunnerRequest if message["type"] in ("dry_run", "execute") else RunnerResult
    model.model_validate(message)


def test_valid_vectors_cover_every_message_type() -> None:
    types = {v["message"]["type"] for v in VECTORS if v["valid"]}
    assert types == {"execute", "dry_run", "dry_run_result", "progress", "execution_result"}


def test_execute_signed_with_link_key_is_a_real_link_signature() -> None:
    # The refused vector is not garbage: it verifies with the wrong key, which is
    # exactly what the runner must refuse for an execute (SEC-002).
    message = by_name("execute_signed_with_link_key_is_refused")["message"]
    assert verify(message, KEYS["RUNNER_LINK_KEY"])
    assert not verify(message, KEYS["ACTION_SIGNING_KEY"])


def test_canonical_json_is_sorted_compact_utf8_and_ignores_sig() -> None:
    message = {"b": 1, "a": {"y": "…", "x": None}, "sig": "ignored"}
    assert canonical_json(message) == '{"a":{"x":null,"y":"…"},"b":1}'.encode()
    assert b"\xe2\x80\xa6" in canonical_json(message)


def test_progress_vector_keeps_non_ascii_as_utf8() -> None:
    vector = by_name("progress_with_non_ascii_message")
    assert "…" in vector["canonical"]
    assert "\\u2026" not in vector["canonical"]


def test_verify_rejects_missing_or_bad_sig() -> None:
    message = dict(by_name("execute_signed_with_action_key")["message"])
    key = KEYS["ACTION_SIGNING_KEY"]
    assert not verify({k: v for k, v in message.items() if k != "sig"}, key)
    assert not verify({**message, "sig": 12345}, key)
    assert not verify({**message, "sig": "0" * 64}, key)


@pytest.mark.parametrize("value", ["00" * 31, "zz" * 32, "abc"])
def test_short_or_malformed_keys_are_refused(value: str) -> None:
    with pytest.raises(ValueError):
        key_from_hex(value)


def test_key_for_each_message_type() -> None:
    assert key_name_for("execute") == "ACTION_SIGNING_KEY"
    for message_type in ("dry_run", "dry_run_result", "progress", "execution_result"):
        assert key_name_for(message_type) == "RUNNER_LINK_KEY"
    with pytest.raises(ValueError):
        key_name_for("exec_shell")
