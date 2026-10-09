"""C3 WebSocket messages: every fixture validates, one fixture per message, envelope rules."""

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from oncallpilot_contracts import domain, ws

CONTRACTS_DIR = Path(__file__).resolve().parents[2]
WS_FIXTURES = CONTRACTS_DIR / "fixtures" / "ws"
CLIENT_TYPES = {"auth", "resume", "pong"}
CONTROL_TYPES = {"hello", "ping", "resync_required"}
EVENTS = {
    "incident.opened",
    "incident.updated",
    "evidence.added",
    "hypothesis.updated",
    "proposal.created",
    "proposal.updated",
    "execution.progress",
    "incident.resolved",
}


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((WS_FIXTURES / f"{name}.json").read_text("utf-8"))
    return data


def fixture_names() -> list[str]:
    return sorted(path.stem for path in WS_FIXTURES.glob("*.json"))


def test_one_fixture_per_message() -> None:
    assert set(fixture_names()) == CLIENT_TYPES | CONTROL_TYPES | EVENTS


@pytest.mark.parametrize("name", fixture_names())
def test_fixture_validates(name: str) -> None:
    data = fixture(name)
    if name in CLIENT_TYPES:
        message: Any = ws.WsClientMessage.model_validate(data).root
    else:
        message = ws.WsServerMessage.model_validate(data).root
    assert message.type == data["type"]
    if data["type"] == "event":
        assert message.event == name


def test_events_cover_the_eight_of_architecture() -> None:
    members = get_args(get_args(ws.WsEvent)[0])
    assert {get_args(m.model_fields["event"].annotation)[0] for m in members} == EVENTS


def test_event_data_gets_its_own_model() -> None:
    opened = ws.WsServerMessage.model_validate(fixture("incident.opened")).root
    assert isinstance(opened, ws.IncidentOpenedEvent)
    assert isinstance(opened.data, domain.IncidentSummary)
    proposal = ws.WsServerMessage.model_validate(fixture("proposal.created")).root
    assert isinstance(proposal, ws.ProposalCreatedEvent)
    assert isinstance(proposal.data, domain.Proposal)


def rejects_server(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ws.WsServerMessage.model_validate(data)


def test_unknown_event_or_type_is_rejected() -> None:
    rejects_server({**fixture("incident.updated"), "event": "incident.deleted"})
    rejects_server({"type": "shutdown"})


def test_incident_opened_must_match_envelope() -> None:
    data = fixture("incident.opened")
    rejects_server({**data, "state_version": 2})
    rejects_server({**data, "incident_id": "00000000-0000-4000-8000-000000000999"})


def test_proposal_created_must_belong_to_incident() -> None:
    rejects_server(
        {**fixture("proposal.created"), "incident_id": "00000000-0000-4000-8000-000000000999"}
    )


def test_evidence_added_needs_payload_truncated() -> None:
    data = fixture("evidence.added")
    del data["data"]["payload_truncated"]
    rejects_server(data)


def test_event_data_must_fit_its_event() -> None:
    data = fixture("incident.resolved")
    rejects_server({**data, "data": fixture("incident.updated")["data"]})


@pytest.mark.parametrize("event_id", ["", "abc", "1791968465000", "1791968465000-x"])
def test_event_id_is_a_stream_id(event_id: str) -> None:
    rejects_server({**fixture("incident.updated"), "event_id": event_id})


def test_proposal_updated_superseded_rule() -> None:
    data = fixture("proposal.updated")
    rejects_server({**data, "data": {**data["data"], "status": "superseded"}})


def test_client_messages() -> None:
    with pytest.raises(ValidationError):
        ws.WsClientMessage.model_validate({"type": "auth", "token": ""})
    with pytest.raises(ValidationError):
        ws.WsClientMessage.model_validate({"type": "pong", "extra": 1})


def test_doc_names_close_codes_and_events() -> None:
    text = (CONTRACTS_DIR / "ws-protocol.md").read_text("utf-8")
    for code in (ws.CLOSE_UNAUTHORIZED, ws.CLOSE_AUTH_TIMEOUT):
        assert str(code) in text
    for event in EVENTS:
        assert f"`{event}`" in text, event
