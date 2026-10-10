"""C9 push payload: exactly five string keys, and the delivery rules in push.md."""

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError
from samples import INCIDENT_ID

from oncallpilot_contracts import push

PUSH_DOC = Path(__file__).resolve().parents[2] / "push.md"


def payload() -> dict[str, Any]:
    return {
        "type": "proposal.created",
        "incident_id": INCIDENT_ID,
        "severity": "sev1",
        "service": "api",
        "title": "api error rate 31%",
    }


def test_payload_is_a_flat_map_of_strings() -> None:
    model = push.PushPayload.model_validate(payload())
    data = json.loads(model.model_dump_json())
    assert set(data) == {"type", "incident_id", "severity", "service", "title"}
    assert all(isinstance(value, str) for value in data.values())  # FCM data values


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "evidence.added"},
        {"severity": "sev0"},
        {"service": "backend-api"},
        {"title": ""},
        {"title": "x" * 121},
        {"summary": "Release 1.5.0 fails at checkout"},
        {"log_line": "Traceback (most recent call last): ..."},
    ],
)
def test_invalid_payload(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        push.PushPayload.model_validate({**payload(), **overrides})


def test_priority_for_every_type() -> None:
    assert set(push.PRIORITY) == set(get_args(push.PushType))
    assert push.PRIORITY["incident.resolved"] == "normal"
    assert {p for t, p in push.PRIORITY.items() if t != "incident.resolved"} == {"high"}


def test_doc_covers_channel_and_types() -> None:
    text = PUSH_DOC.read_text("utf-8")
    assert push.ANDROID_CHANNEL_ID in text
    for push_type in get_args(push.PushType):
        assert f"`{push_type}`" in text, push_type
